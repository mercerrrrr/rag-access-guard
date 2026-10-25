from typing import Literal

import pytest

from rag_access_guard_api.adapters.embeddings import (
    E5EmbeddingAdapter,
    EmbeddingError,
    IsolatedE5EmbeddingAdapter,
    get_embedding_adapter,
)
from rag_access_guard_api.adapters.inference_runtime import InferenceRuntime, bind_runtime


@pytest.mark.anyio
async def test_small_profile_reuses_only_query_computation(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given the small profile and a physical encoder returning a normalized vector.
    monkeypatch.setenv("RAG_ACCESS_GUARD_MODEL_PROFILE", "qwen25-extractive-small-demo-v2")
    monkeypatch.setenv("RAG_ACCESS_GUARD_E5_EXECUTION", "isolated")
    vector = (1.0, *(0.0 for _ in range(383)))
    calls: list[str] = []

    def encode(
        _self: E5EmbeddingAdapter, texts: tuple[str, ...], kind: Literal["query", "passage"]
    ) -> tuple[tuple[float, ...], ...]:
        calls.append(kind)
        return tuple(vector for _ in texts)

    monkeypatch.setattr(E5EmbeddingAdapter, "_encode", encode)
    monkeypatch.setattr(IsolatedE5EmbeddingAdapter, "_encode", encode)
    owner = InferenceRuntime()
    try:
        with bind_runtime(owner):
            # When request-scoped adapters repeat a question and index a passage.
            assert await get_embedding_adapter().embed_query("question") == vector
            assert await get_embedding_adapter().embed_query("question") == vector
            assert await get_embedding_adapter().embed_passages(("question",)) == (vector,)
        # Then physical encoding is reused, but all requests still enter admission.
        assert calls == ["query", "passage"]
        assert owner.accepted_embedding_jobs == 3
    finally:
        owner.close()


def test_small_profile_still_rejects_resident_e5(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a request to keep E5 resident in the small demonstration profile.
    monkeypatch.setenv("RAG_ACCESS_GUARD_MODEL_PROFILE", "qwen25-extractive-small-demo-v2")
    monkeypatch.setenv("RAG_ACCESS_GUARD_E5_EXECUTION", "resident")
    # When the factory is called, then query caching must not permit model residency.
    with pytest.raises(EmbeddingError):
        _ = get_embedding_adapter()
