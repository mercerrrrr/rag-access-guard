from pathlib import Path
from typing import Final

import httpx2
import pytest
from pydantic import JsonValue, TypeAdapter
from tests.unit.test_demo_ownership import config_for

from rag_access_guard_api.adapters.model_tokens import ModelTokenCounter, get_model_counter
from rag_access_guard_api.adapters.ollama import OllamaAdapter
from rag_access_guard_api.config import Settings
from rag_access_guard_api.demo.contracts import DemoConfig
from rag_access_guard_api.schemas.generation import GenerationUnavailable
from rag_access_guard_api.services.evaluation_identity import profile_hash
from rag_access_guard_api.services.model_profiles import DEMO_PROFILE, get_model_profile

SMALL_ID: Final = "qwen25-instruct-small-demo-v1"
JSON: Final = TypeAdapter[JsonValue](JsonValue)


def select_small_counter(monkeypatch: pytest.MonkeyPatch) -> ModelTokenCounter:
    profile = get_model_profile(SMALL_ID)
    monkeypatch.setenv("RAG_ACCESS_GUARD_MODEL_PROFILE", SMALL_ID)
    monkeypatch.setenv(
        "RAG_ACCESS_GUARD_MODEL_TOKENIZER_PATH",
        str(Path(".cache/qwen3") / profile.manifest.tokenizer_revision / "tokenizer.json"),
    )
    return get_model_counter()


@pytest.mark.anyio
async def test_small_adapter_uses_verified_model_and_bounded_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = select_small_counter(monkeypatch)

    def respond(request: httpx2.Request) -> httpx2.Response:
        body = JSON.validate_json(request.content)
        assert isinstance(body, dict)
        assert body["model"] == "qwen2.5:1.5b-instruct-q4_K_M"
        assert body["options"] == {
            "num_ctx": 4096,
            "num_predict": 512,
            "temperature": 0,
            "seed": 42,
        }
        assert body["stream"] is False
        assert body["messages"] == DEMO_PROFILE.messages(
            user_input="QUESTION", system_supplied_context="CONTEXT"
        )
        return httpx2.Response(
            200,
            json={
                "done": True,
                "done_reason": "stop",
                "message": {"role": "assistant", "content": "ANSWER"},
            },
        )

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(respond), base_url="http://127.0.0.1:11435"
    ) as client:
        answer = await OllamaAdapter(client).generate(
            user_input="QUESTION", system_supplied_context="CONTEXT"
        )
    assert answer == "ANSWER"


def test_small_counter_rejects_foreign_tokenizer_and_keeps_separate_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = get_model_counter("qwen3-instruct-demo-v1")
    small = select_small_counter(monkeypatch)
    assert small.identity != original.identity
    assert profile_hash(get_model_profile(SMALL_ID)) != profile_hash(DEMO_PROFILE)
    assert get_model_counter() is small
    with pytest.raises(GenerationUnavailable):
        _ = ModelTokenCounter(small.manifest, original.tokenizer_path)


@pytest.mark.anyio
async def test_small_full_request_overflow_does_not_reach_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = select_small_counter(monkeypatch)
    context = " x" * 3800
    assert counter.count(context) < 5000
    assert (
        counter.count_request(user_input="QUESTION", system_supplied_context=context) + 512 > 4096
    )
    paths: list[str] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        paths.append(request.url.path)
        return httpx2.Response(500)

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(respond), base_url="http://127.0.0.1:11435"
    ) as client:
        with pytest.raises(GenerationUnavailable):
            _ = await OllamaAdapter(client).generate(
                user_input="QUESTION", system_supplied_context=context
            )
    assert paths == []


def test_private_small_selection_is_explicit_and_does_not_change_default(tmp_path: Path) -> None:
    original = config_for(tmp_path)
    fields = original.model_dump(mode="python")
    fields["database_url"] = original.database_url
    fields["model_profile"] = SMALL_ID
    selected = DemoConfig.model_validate(fields)
    assert Settings(model_profile=selected.model_profile).model_profile == SMALL_ID
    assert Settings().model_profile == "qwen3-thinking-legacy-v1"


def test_small_renderer_matches_runtime_adjacent_user_collation() -> None:
    profile = get_model_profile(SMALL_ID)
    messages = profile.messages(user_input="QUESTION 👋", system_supplied_context="CONTEXT")
    expected = (
        f"<|im_start|>system\n{messages[0]['content']}<|im_end|>\n"
        "<|im_start|>user\nsystem_supplied_context:\nCONTEXT\n\n"
        "user_input:\nQUESTION 👋<|im_end|>\n<|im_start|>assistant\n"
    )

    rendered = profile.render(user_input="QUESTION 👋", system_supplied_context="CONTEXT")

    assert rendered == expected
    assert rendered.count("<|im_start|>user\n") == 1
