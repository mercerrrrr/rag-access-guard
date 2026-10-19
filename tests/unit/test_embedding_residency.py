from collections.abc import Callable, Iterator
from pathlib import Path
from threading import Event
from typing import ClassVar, Literal
from weakref import ReferenceType, ref

import anyio
import pytest
from anyio.to_thread import run_sync

from rag_access_guard_api.adapters import e5_runtime
from rag_access_guard_api.adapters.embeddings import (
    E5EmbeddingAdapter,
    EmbeddingError,
    get_embedding_adapter,
)
from rag_access_guard_api.adapters.inference_runtime import InferenceRuntime

VECTOR = ((1.0, *(0.0 for _ in range(383))),)


class CountingRuntime:
    constructed: int = 0
    fail: bool = False
    failure: ClassVar[type[Exception]] = RuntimeError

    def __init__(self, path: Path) -> None:
        del path
        CountingRuntime.constructed += 1
        references.append(ref(self))

    def encode(
        self, texts: tuple[str, ...], kind: Literal["query", "passage"]
    ) -> tuple[tuple[float, ...], ...]:
        del texts, kind
        if self.fail:
            message = "fixture inference failure"
            raise self.failure(message)
        return VECTOR


references: list[ReferenceType[CountingRuntime]] = []


@pytest.fixture(autouse=True)
def isolated_runtime_cache(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    e5_runtime.get_runtime.cache_clear()
    CountingRuntime.constructed = 0
    CountingRuntime.fail = False
    CountingRuntime.failure = RuntimeError
    references.clear()
    monkeypatch.setattr(e5_runtime, "E5Runtime", CountingRuntime)
    try:
        yield
    finally:
        e5_runtime.get_runtime.cache_clear()


@pytest.fixture
def inference_runtime() -> Iterator[InferenceRuntime]:
    runtime = InferenceRuntime()
    try:
        yield runtime
    finally:
        runtime.close()


@pytest.mark.anyio
async def test_transient_model_is_released_after_each_physical_encoding(
    tmp_path: Path, inference_runtime: InferenceRuntime
) -> None:
    adapter = E5EmbeddingAdapter(tmp_path, runtime=inference_runtime, retain_model=False)

    first = await adapter.embed_passages(("question",))
    assert e5_runtime.get_runtime.cache_info().currsize == 0
    second = await adapter.embed_passages(("question",))

    assert first == second == VECTOR
    assert CountingRuntime.constructed == 2
    assert e5_runtime.get_runtime.cache_info().currsize == 0


@pytest.mark.anyio
@pytest.mark.parametrize("failure", [RuntimeError, EmbeddingError])
async def test_transient_model_is_released_after_inference_error(
    tmp_path: Path,
    inference_runtime: InferenceRuntime,
    failure: type[Exception],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = E5EmbeddingAdapter(tmp_path, runtime=inference_runtime, retain_model=False)
    CountingRuntime.fail = True
    CountingRuntime.failure = failure
    alive_at_physical_exit: list[bool] = []
    original_embedding = inference_runtime.run_embedding

    async def inspect[T](call: Callable[[], T]) -> T:
        def observed_call() -> T:
            try:
                return call()
            finally:
                alive_at_physical_exit.append(
                    any(reference() is not None for reference in references)
                )

        return await original_embedding(observed_call)

    monkeypatch.setattr(inference_runtime, "run_embedding", inspect)

    with pytest.raises(EmbeddingError) as observed:
        _ = await adapter.embed_query("question")

    assert e5_runtime.get_runtime.cache_info().currsize == 0
    assert isinstance(observed.value, EmbeddingError)
    assert alive_at_physical_exit == [False]
    assert references[-1]() is None


@pytest.mark.anyio
async def test_existing_adapters_keep_resident_model_by_default(
    tmp_path: Path, inference_runtime: InferenceRuntime
) -> None:
    adapter = E5EmbeddingAdapter(tmp_path, runtime=inference_runtime)

    assert await adapter.embed_passages(("question",)) == VECTOR
    assert await adapter.embed_passages(("question",)) == VECTOR
    assert CountingRuntime.constructed == 1
    e5_runtime.get_runtime.cache_clear()


@pytest.mark.anyio
async def test_cancellation_retains_model_until_physical_encoding_exits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entered, release, physical_done = Event(), Event(), Event()

    def block(
        _self: CountingRuntime, _texts: tuple[str, ...], _kind: Literal["query", "passage"]
    ) -> tuple[tuple[float, ...], ...]:
        entered.set()
        assert release.wait(5)
        return VECTOR

    monkeypatch.setattr(CountingRuntime, "encode", block)
    runtime = InferenceRuntime()
    original_embedding = runtime.run_embedding

    async def signal[T](call: Callable[[], T]) -> T:
        def observed() -> T:
            try:
                return call()
            finally:
                physical_done.set()

        return await original_embedding(observed)

    monkeypatch.setattr(runtime, "run_embedding", signal)
    adapter = E5EmbeddingAdapter(tmp_path, runtime=runtime, retain_model=False)
    try:
        with anyio.CancelScope() as scope:
            async with anyio.create_task_group() as group:

                async def cancel() -> None:
                    assert await run_sync(entered.wait, 2)
                    scope.cancel()

                _ = group.start_soon(cancel)
                _ = await adapter.embed_query("question")
        assert runtime.snapshot().embeddings == "busy"
        assert e5_runtime.get_runtime.cache_info().currsize == 1
        release.set()
        assert await run_sync(physical_done.wait, 3)
        assert e5_runtime.get_runtime.cache_info().currsize == 0
    finally:
        release.set()
        runtime.close()


@pytest.mark.parametrize(
    ("profile", "resident"),
    [
        ("qwen35-extractive-demo-v1", False),
        ("qwen25-extractive-small-demo-v1", False),
        ("qwen35-extractive-cpu-demo-v1", False),
        ("qwen25-instruct-small-demo-v1", True),
        ("qwen3-instruct-demo-v1", True),
        ("qwen3-thinking-legacy-v1", True),
    ],
)
def test_factory_changes_lifetime_only_for_explicit_extractive_profile(
    monkeypatch: pytest.MonkeyPatch, profile: str, *, resident: bool
) -> None:
    monkeypatch.setenv("RAG_ACCESS_GUARD_MODEL_PROFILE", profile)
    adapter = get_embedding_adapter()

    assert isinstance(adapter, E5EmbeddingAdapter)
    assert adapter.retain_model is resident
