from pathlib import Path

import httpx2
import pytest
from pydantic import JsonValue, TypeAdapter
from tests.unit.test_extractive_model_profile import PASSAGE, context_for

from rag_access_guard_api.adapters.embeddings import E5EmbeddingAdapter, get_embedding_adapter
from rag_access_guard_api.adapters.model_tokens import ModelTokenCounter
from rag_access_guard_api.adapters.ollama import OllamaAdapter
from rag_access_guard_api.config import Settings
from rag_access_guard_api.schemas.generation import GenerationUnavailable
from rag_access_guard_api.services.evaluation_identity import profile_hash
from rag_access_guard_api.services.model_profiles import get_model_profile

PROFILE = "qwen35-extractive-text-demo-v1"


def test_text_profile_preserves_original_profiles_and_has_distinct_identity() -> None:
    profile = get_model_profile(PROFILE)
    assert profile.manifest.model_name == "rag-qwen35-text:2b-q4_k_m"
    assert (
        profile.manifest.model_digest
        == "84e2c930d4ab82f0e2ec10fc0f7497b59085db8304d6c2f979cbafc4009e5c1c"
    )
    assert profile.options() == {"num_ctx": 4096, "num_predict": 512, "temperature": 0, "seed": 42}
    original = get_model_profile("qwen35-extractive-demo-v1")
    assert (
        profile_hash(original) == "365be9bdbfe583cd241c5274d0119e3ccd2100f039eb8c5c053ac1c664cc664e"
    )
    assert profile_hash(profile) != profile_hash(original)
    assert Settings().model_profile == "qwen3-thinking-legacy-v1"


@pytest.mark.anyio
@pytest.mark.parametrize("selection", ['{"paragraph_ids":[0]}', '{"paragraph_ids":[999]}'])
async def test_text_adapter_uses_the_same_bounded_source_selection(
    monkeypatch: pytest.MonkeyPatch, selection: str
) -> None:
    profile = get_model_profile(PROFILE)
    monkeypatch.setenv("RAG_ACCESS_GUARD_MODEL_PROFILE", PROFILE)
    monkeypatch.setenv(
        "RAG_ACCESS_GUARD_MODEL_TOKENIZER_PATH",
        str(Path(".cache/qwen3") / profile.manifest.tokenizer_revision / "tokenizer.json"),
    )
    question = "QUESTION_WITH_USER_PASTE"
    context = context_for(PASSAGE)

    def respond(request: httpx2.Request) -> httpx2.Response:
        payload = TypeAdapter[JsonValue](JsonValue).validate_json(request.content)
        assert isinstance(payload, dict)
        assert payload["model"] == "rag-qwen35-text:2b-q4_k_m"
        assert payload["think"] is False
        assert payload["stream"] is False
        assert payload["options"] == {
            "num_ctx": 4096,
            "num_predict": 512,
            "temperature": 0,
            "seed": 42,
        }
        messages = payload["messages"]
        assert isinstance(messages, list)
        user = messages[1]
        assert isinstance(user, dict)
        assert isinstance(user["content"], str)
        managed, separator, current = user["content"].partition("\n\nuser_input:\n")
        assert separator
        assert current == question
        assert question not in managed
        return httpx2.Response(
            200,
            json={
                "done": True,
                "done_reason": "stop",
                "message": {"role": "assistant", "content": selection},
            },
        )

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(respond), base_url="http://127.0.0.1:11435"
    ) as client:
        if selection == '{"paragraph_ids":[999]}':
            with pytest.raises(GenerationUnavailable):
                _ = await OllamaAdapter(client).generate(
                    user_input=question, system_supplied_context=context
                )
        else:
            answer = await OllamaAdapter(client).generate(
                user_input=question, system_supplied_context=context
            )
            assert PASSAGE in answer
            assert question not in answer
            assert ModelTokenCounter(profile.manifest).count(answer) <= 512


def test_text_profile_releases_transient_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_ACCESS_GUARD_MODEL_PROFILE", PROFILE)
    adapter = get_embedding_adapter()
    assert isinstance(adapter, E5EmbeddingAdapter)
    assert not adapter.retain_model
