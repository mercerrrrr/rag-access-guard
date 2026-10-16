from pathlib import Path
from typing import Final

import httpx2
import pytest
from pydantic import JsonValue, TypeAdapter
from tests.unit.test_demo_ownership import config_for

from rag_access_guard_api.adapters.model_tokens import get_model_counter
from rag_access_guard_api.adapters.ollama import OllamaAdapter
from rag_access_guard_api.config import Settings
from rag_access_guard_api.demo.contracts import DemoConfig
from rag_access_guard_api.schemas.generation import GenerationUnavailable
from rag_access_guard_api.services.evaluation_identity import profile_hash
from rag_access_guard_api.services.model_profiles import DEMO_PROFILE, get_model_profile

COMPACT_ID: Final = "qwen3-instruct-compact-demo-v1"
JSON: Final = TypeAdapter[JsonValue](JsonValue)


@pytest.mark.anyio
async def test_compact_adapter_sends_smaller_window_with_original_model_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_ACCESS_GUARD_MODEL_PROFILE", COMPACT_ID)

    def respond(request: httpx2.Request) -> httpx2.Response:
        body = JSON.validate_json(request.content)
        assert isinstance(body, dict)
        assert request.url.path == "/api/chat"
        assert body["model"] == "qwen3:4b-instruct-2507-q4_K_M"
        assert body["options"] == {
            "num_ctx": 4096,
            "num_predict": 512,
            "temperature": 0,
            "seed": 42,
        }
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


def test_compact_counter_preserves_rendering_but_has_separate_budget_identity() -> None:
    original = get_model_counter("qwen3-instruct-demo-v1")
    compact = get_model_counter(COMPACT_ID)
    assert compact.count_request(user_input="QUESTION", system_supplied_context="CONTEXT") == (
        original.count_request(user_input="QUESTION", system_supplied_context="CONTEXT")
    )
    assert compact.identity != original.identity
    assert profile_hash(get_model_profile(COMPACT_ID)) != profile_hash(DEMO_PROFILE)


@pytest.mark.anyio
async def test_compact_request_overflow_is_rejected_before_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_ACCESS_GUARD_MODEL_PROFILE", COMPACT_ID)
    counter = get_model_counter(COMPACT_ID)
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


def test_private_demo_config_resolves_the_same_compact_profile(tmp_path: Path) -> None:
    original = config_for(tmp_path)
    fields = original.model_dump(mode="python")
    fields["database_url"] = original.database_url
    fields["model_profile"] = COMPACT_ID
    selected = DemoConfig.model_validate(fields)
    settings = Settings(model_profile=selected.model_profile)
    counter = get_model_counter(settings.model_profile)
    assert counter.manifest.context_window == 4096
    assert counter.manifest.model_digest == DEMO_PROFILE.manifest.model_digest
