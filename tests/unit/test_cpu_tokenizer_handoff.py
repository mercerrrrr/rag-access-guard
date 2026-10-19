import gc
from collections.abc import Iterator
from hashlib import sha256
from pathlib import Path
from threading import get_ident
from weakref import ReferenceType, finalize, ref

import httpx2
import pytest
from tests.unit.test_cpu_extractive_profile import PROFILE
from tests.unit.test_extractive_model_profile import PASSAGE, context_for

from rag_access_guard_api.adapters import tokenizer
from rag_access_guard_api.adapters.ollama import OllamaAdapter
from rag_access_guard_api.adapters.tokenizer import E5TokenCounter
from rag_access_guard_api.schemas.generation import GenerationUnavailable
from rag_access_guard_api.services.model_profiles import get_model_profile


class CyclicTokenCounter(E5TokenCounter):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.cycle: CyclicTokenCounter = self


@pytest.fixture
def cached_tokenizer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[ReferenceType[E5TokenCounter]]:
    raw = (
        '{"version":"1.0","truncation":null,"padding":null,"added_tokens":[],"normalizer":null,'
        '"pre_tokenizer":null,"post_processor":null,"decoder":null,"model":'
        '{"type":"WordLevel","vocab":{"[UNK]":0,"hello":1},"unk_token":"[UNK]"}}'
    )
    path = tmp_path / "tokenizer.json"
    _ = path.write_text(raw, encoding="utf-8")
    monkeypatch.setenv("RAG_ACCESS_GUARD_TOKENIZER_PATH", str(path))
    monkeypatch.setattr(tokenizer, "TOKENIZER_SHA256", sha256(raw.encode()).hexdigest())
    monkeypatch.setattr(tokenizer, "E5TokenCounter", CyclicTokenCounter)
    tokenizer.get_tokenizer.cache_clear()
    counter = tokenizer.get_tokenizer()
    assert counter.count("hello") == 1
    reference = ref(counter)
    del counter

    def reject_new_load(_path: Path) -> E5TokenCounter:
        pytest.fail("handoff must not construct another ingestion tokenizer")

    monkeypatch.setattr(tokenizer, "E5TokenCounter", reject_new_load)
    try:
        yield reference
    finally:
        tokenizer.get_tokenizer.cache_clear()
        _ = gc.collect()


@pytest.mark.anyio
@pytest.mark.parametrize("status", [200, 503])
async def test_cpu_handoff_frees_cached_counter_before_dispatch_off_event_loop(
    cached_tokenizer: ReferenceType[E5TokenCounter], status: int
) -> None:
    # Given a cached cyclic resource with no active caller references.
    resource = cached_tokenizer()
    assert resource is not None
    collection_threads: list[int] = []

    def collected() -> None:
        collection_threads.append(get_ident())

    _ = finalize(resource, collected)
    del resource
    loop_thread = get_ident()

    def respond(_: httpx2.Request) -> httpx2.Response:
        assert cached_tokenizer() is None
        assert tokenizer.get_tokenizer.cache_info().currsize == 0
        assert len(collection_threads) == 1
        assert collection_threads[0] != loop_thread
        return httpx2.Response(
            status,
            json={
                "done": True,
                "done_reason": "stop",
                "message": {"role": "assistant", "content": '{"paragraph_ids":[0]}'},
            },
        )

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(respond), base_url="http://127.0.0.1:11435"
    ) as client:
        # When the CPU request dispatches, then the cache releases before either HTTP outcome.
        adapter = OllamaAdapter(client, manifest=get_model_profile(PROFILE).manifest)
        if status == 200:
            _ = await adapter.generate(
                user_input="QUESTION", system_supplied_context=context_for(PASSAGE)
            )
        else:
            with pytest.raises(GenerationUnavailable):
                _ = await adapter.generate(
                    user_input="QUESTION", system_supplied_context=context_for(PASSAGE)
                )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "profile_id",
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
async def test_existing_profiles_preserve_ingestion_cache_before_dispatch(
    cached_tokenizer: ReferenceType[E5TokenCounter], profile_id: str
) -> None:
    # Given a cached ingestion counter and a pre-existing profile.
    def respond(_: httpx2.Request) -> httpx2.Response:
        assert cached_tokenizer() is not None
        assert tokenizer.get_tokenizer.cache_info().currsize == 1
        return httpx2.Response(
            200,
            json={
                "done": True,
                "done_reason": "stop",
                "message": {"role": "assistant", "content": '{"paragraph_ids":[0]}'},
            },
        )

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(respond), base_url="http://127.0.0.1:11435"
    ) as client:
        # When an old profile generates, then its cache lifetime stays unchanged.
        _ = await OllamaAdapter(client, manifest=get_model_profile(profile_id).manifest).generate(
            user_input="QUESTION", system_supplied_context=context_for(PASSAGE)
        )


@pytest.mark.anyio
async def test_cpu_handoff_waits_until_request_budget_validation_succeeds(
    cached_tokenizer: ReferenceType[E5TokenCounter],
) -> None:
    # Given an input that exceeds the existing byte budget.
    def respond(_: httpx2.Request) -> httpx2.Response:
        pytest.fail("invalid request must not dispatch")

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(respond), base_url="http://127.0.0.1:11435"
    ) as client:
        # When validation rejects it, then retain the cached counter.
        with pytest.raises(GenerationUnavailable):
            _ = await OllamaAdapter(client, manifest=get_model_profile(PROFILE).manifest).generate(
                user_input="x" * 16385, system_supplied_context=context_for(PASSAGE)
            )
    assert cached_tokenizer() is not None
    assert tokenizer.get_tokenizer.cache_info().currsize == 1


@pytest.mark.anyio
async def test_cpu_handoff_preserves_counter_held_by_an_active_caller(
    cached_tokenizer: ReferenceType[E5TokenCounter],
) -> None:
    # Given an independent caller still using the cached counter.
    active = cached_tokenizer()
    assert active is not None

    def respond(_: httpx2.Request) -> httpx2.Response:
        assert tokenizer.get_tokenizer.cache_info().currsize == 0
        assert active.count("hello") == 1
        return httpx2.Response(
            200,
            json={
                "done": True,
                "done_reason": "stop",
                "message": {"role": "assistant", "content": '{"paragraph_ids":[0]}'},
            },
        )

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(respond), base_url="http://127.0.0.1:11435"
    ) as client:
        # When the cache releases, then active caller references remain usable.
        _ = await OllamaAdapter(client, manifest=get_model_profile(PROFILE).manifest).generate(
            user_input="QUESTION", system_supplied_context=context_for(PASSAGE)
        )


@pytest.mark.anyio
@pytest.mark.parametrize("failure", [RuntimeError, MemoryError])
async def test_cpu_handoff_failure_prevents_model_dispatch(
    cached_tokenizer: ReferenceType[E5TokenCounter],
    monkeypatch: pytest.MonkeyPatch,
    failure: type[RuntimeError] | type[MemoryError],
) -> None:
    # Given a failing cache-collection handoff.
    assert cached_tokenizer() is not None

    def collect() -> int:
        raise failure

    def respond(_: httpx2.Request) -> httpx2.Response:
        pytest.fail("failed handoff must not dispatch inference")

    with monkeypatch.context() as scoped:
        scoped.setattr(gc, "collect", collect)
        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(respond), base_url="http://127.0.0.1:11435"
        ) as client:
            # When collection fails, then return the existing unavailable outcome before sending.
            with pytest.raises(GenerationUnavailable):
                _ = await OllamaAdapter(
                    client, manifest=get_model_profile(PROFILE).manifest
                ).generate(user_input="QUESTION", system_supplied_context=context_for(PASSAGE))
