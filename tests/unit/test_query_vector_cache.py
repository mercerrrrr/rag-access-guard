import gc
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import Literal
from weakref import ref

import anyio
import pytest
from anyio.to_thread import run_sync

from rag_access_guard_api.adapters.embeddings import (
    E5EmbeddingAdapter,
    IsolatedE5EmbeddingAdapter,
    get_embedding_adapter,
)
from rag_access_guard_api.adapters.inference_runtime import (
    InferenceRuntime,
    bind_runtime,
)
from rag_access_guard_api.schemas.embedding_vectors import EmbeddingError
from rag_access_guard_api.schemas.generation import InferenceBusyError, InferenceUnavailableError

VECTOR = (1.0, *(0.0 for _ in range(383)))
CPU = "qwen35-extractive-cpu-demo-v1"


@pytest.fixture
def runtime() -> Iterator[InferenceRuntime]:
    owner = InferenceRuntime()
    with bind_runtime(owner):
        try:
            yield owner
        finally:
            owner.close()


@pytest.fixture
def encodings(monkeypatch: pytest.MonkeyPatch) -> list[tuple[tuple[str, ...], str]]:
    calls: list[tuple[tuple[str, ...], str]] = []

    def encode(
        _self: E5EmbeddingAdapter, texts: tuple[str, ...], kind: Literal["query", "passage"]
    ) -> tuple[tuple[float, ...], ...]:
        calls.append((texts, kind))
        return tuple(VECTOR for _ in texts)

    monkeypatch.setenv("RAG_ACCESS_GUARD_MODEL_PROFILE", CPU)
    monkeypatch.setattr(E5EmbeddingAdapter, "_encode", encode)
    monkeypatch.setattr(IsolatedE5EmbeddingAdapter, "_encode", encode)
    return calls


@pytest.mark.anyio
async def test_exact_queries_reuse_vectors_across_adapters_but_passages_do_not(
    runtime: InferenceRuntime, encodings: list[tuple[tuple[str, ...], str]]
) -> None:
    # Given fresh adapters sharing one application runtime.
    first, second = get_embedding_adapter(), get_embedding_adapter()
    # When identical queries and passages are embedded.
    assert await first.embed_query("private question") == VECTOR
    assert await second.embed_query("private question") == VECTOR
    assert await first.embed_passages(("private question",)) == (VECTOR,)
    assert await second.embed_passages(("private question",)) == (VECTOR,)
    # Then only query computation is reused; every call still enters physical admission.
    assert encodings == [
        (("private question",), "query"),
        *(((("private question",), "passage"),) * 2),
    ]
    assert runtime.accepted_embedding_jobs == 4


@pytest.mark.anyio
@pytest.mark.usefixtures("runtime")
@pytest.mark.parametrize(
    "profile",
    [
        "qwen3-thinking-legacy-v1",
        "qwen3-instruct-demo-v1",
        "qwen3-instruct-compact-demo-v1",
        "qwen25-instruct-small-demo-v1",
        "qwen25-extractive-small-demo-v1",
        "qwen35-extractive-demo-v1",
        "qwen35-extractive-text-demo-v1",
    ],
)
async def test_old_profiles_preserve_query_computation(
    encodings: list[tuple[tuple[str, ...], str]],
    monkeypatch: pytest.MonkeyPatch,
    profile: str,
) -> None:
    # Given an existing operator profile.
    monkeypatch.setenv("RAG_ACCESS_GUARD_MODEL_PROFILE", profile)
    # When a query repeats, then its original uncached behavior is retained.
    for _ in range(2):
        assert await get_embedding_adapter().embed_query("question") == VECTOR
    assert len(encodings) == 2


@pytest.mark.anyio
@pytest.mark.usefixtures("runtime")
async def test_key_separates_exact_query_bytes_paths_and_model_identity(
    encodings: list[tuple[tuple[str, ...], str]],
    tmp_path: Path,
) -> None:
    # Given a CPU adapter and byte-distinct questions, including Unicode normalization.
    adapter = get_embedding_adapter()
    assert isinstance(adapter, E5EmbeddingAdapter)
    queries = ("question", " question", "question ", "é", "e\u0301")
    # When each input repeats, then only the exact same artifact/query key hits.
    for query in (*queries, *queries):
        assert await adapter.embed_query(query) == VECTOR
    for changed in (
        replace(adapter, path=tmp_path / "other"),
        replace(adapter, revision="other-revision"),
        replace(adapter, model_id="other-model"),
    ):
        assert await changed.embed_query("question") == VECTOR
    assert len(encodings) == 8


@pytest.mark.anyio
@pytest.mark.usefixtures("runtime")
async def test_cache_is_lru_bounded_per_runtime(
    encodings: list[tuple[tuple[str, ...], str]],
) -> None:
    # Given 128 distinct cached query vectors.
    adapter = get_embedding_adapter()
    for index in range(128):
        _ = await adapter.embed_query(str(index))
    # When key zero is refreshed and another key arrives, then key one is evicted.
    _ = await adapter.embed_query("0")
    _ = await adapter.embed_query("128")
    _ = await adapter.embed_query("0")
    _ = await adapter.embed_query("1")
    assert len(encodings) == 130


@pytest.mark.anyio
async def test_runtime_lifetimes_do_not_share_or_retain_cached_vectors(
    encodings: list[tuple[tuple[str, ...], str]],
) -> None:
    # Given a completed runtime with cached computation.
    owner = InferenceRuntime()
    with bind_runtime(owner):
        _ = await get_embedding_adapter().embed_query("question")
    owner.close()
    weak_owner = ref(owner)
    del owner
    _ = gc.collect()
    # When another runtime repeats the query, then it recomputes without retaining the old owner.
    other = InferenceRuntime()
    try:
        with bind_runtime(other):
            _ = await get_embedding_adapter().embed_query("question")
        assert weak_owner() is None
        assert len(encodings) == 2
    finally:
        other.close()


@pytest.mark.anyio
@pytest.mark.usefixtures("runtime", "encodings")
@pytest.mark.parametrize("failure", ["exception", "dimension", "nan", "norm"])
async def test_failed_or_invalid_encoding_does_not_populate_cache(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    # Given a failing or malformed physical encoder.
    calls: list[str] = []

    def encode(
        _self: E5EmbeddingAdapter, _texts: tuple[str, ...], _kind: Literal["query", "passage"]
    ) -> tuple[tuple[float, ...], ...]:
        calls.append(failure)
        if failure == "exception":
            raise EmbeddingError
        return {
            "dimension": ((1.0,),),
            "nan": ((float("nan"), *VECTOR[1:]),),
            "norm": ((2.0, *VECTOR[1:]),),
        }[failure]

    monkeypatch.setattr(E5EmbeddingAdapter, "_encode", encode)
    monkeypatch.setattr(IsolatedE5EmbeddingAdapter, "_encode", encode)
    # When the same invalid query repeats, then neither response becomes a cache entry.
    for _ in range(2):
        with pytest.raises(EmbeddingError):
            _ = await get_embedding_adapter().embed_query("question")
    assert len(calls) == 2


@pytest.mark.anyio
@pytest.mark.parametrize("state", ["closed", "quarantined", "cancelled"])
async def test_cache_hit_cannot_bypass_unavailable_or_cancelled_admission(
    runtime: InferenceRuntime,
    encodings: list[tuple[tuple[str, ...], str]],
    state: str,
) -> None:
    # Given a previously cached query.
    _ = await get_embedding_adapter().embed_query("question")
    # When admission closes or the caller cancels, then no cached vector is returned.
    if state == "cancelled":
        with anyio.CancelScope() as scope:
            scope.cancel()
            _ = await get_embedding_adapter().embed_query("question")
            pytest.fail("cancelled cache hit must not return")
    else:
        if state == "closed":
            runtime.close()
        else:
            runtime.set_generation_available(available=False)
        with pytest.raises(InferenceUnavailableError):
            _ = await get_embedding_adapter().embed_query("question")
    assert runtime.accepted_embedding_jobs == (2 if state == "quarantined" else 1)
    assert len(encodings) == 1


@pytest.mark.anyio
@pytest.mark.usefixtures("encodings")
async def test_cancelled_physical_encoding_retains_admission_until_actual_exit(
    runtime: InferenceRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a warmed hit and an independent blocked physical query.
    adapter = get_embedding_adapter()
    _ = await adapter.embed_query("hit")
    entered, release, finished = Event(), Event(), Event()

    def block(
        _self: E5EmbeddingAdapter, _texts: tuple[str, ...], _kind: Literal["query", "passage"]
    ) -> tuple[tuple[float, ...], ...]:
        entered.set()
        assert release.wait(5)
        finished.set()
        return (VECTOR,)

    monkeypatch.setattr(E5EmbeddingAdapter, "_encode", block)
    monkeypatch.setattr(IsolatedE5EmbeddingAdapter, "_encode", block)
    try:
        with anyio.CancelScope() as scope:
            async with anyio.create_task_group() as group:

                async def cancel() -> None:
                    assert await run_sync(entered.wait, 2)
                    scope.cancel()

                _ = group.start_soon(cancel)
                _ = await adapter.embed_query("miss")
        assert runtime.snapshot().embeddings == "busy"
        with pytest.raises(InferenceBusyError):
            _ = await adapter.embed_query("hit")
    finally:
        release.set()
        assert await run_sync(finished.wait, 2)


@pytest.mark.anyio
async def test_concurrent_runtime_owners_keep_independent_caches(
    encodings: list[tuple[tuple[str, ...], str]],
) -> None:
    owners = (InferenceRuntime(), InferenceRuntime())

    async def query(owner: InferenceRuntime) -> None:
        with bind_runtime(owner):
            for _ in range(2):
                assert await get_embedding_adapter().embed_query("question") == VECTOR

    try:
        async with anyio.create_task_group() as group:
            for owner in owners:
                _ = group.start_soon(query, owner)
        assert len(encodings) == 2
    finally:
        for owner in owners:
            owner.close()


@pytest.mark.anyio
@pytest.mark.usefixtures("encodings")
async def test_quarantine_during_encoding_discards_the_vector(
    runtime: InferenceRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given physical encoding that quarantines its runtime before returning.
    calls: list[str] = []

    def encode(
        _self: E5EmbeddingAdapter, _texts: tuple[str, ...], _kind: Literal["query", "passage"]
    ) -> tuple[tuple[float, ...], ...]:
        calls.append("encoded")
        runtime.set_generation_available(available=len(calls) > 1)
        return (VECTOR,)

    monkeypatch.setattr(E5EmbeddingAdapter, "_encode", encode)
    monkeypatch.setattr(IsolatedE5EmbeddingAdapter, "_encode", encode)
    # When the worker completes, then its valid vector is neither returned nor retained.
    with pytest.raises(InferenceUnavailableError):
        _ = await get_embedding_adapter().embed_query("question")
    runtime.set_generation_available(available=True)
    assert await get_embedding_adapter().embed_query("question") == VECTOR
    assert len(calls) == 2
