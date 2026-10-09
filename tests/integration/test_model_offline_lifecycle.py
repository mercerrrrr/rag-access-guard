from pathlib import Path
from uuid import uuid4

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from sqlalchemy import text

from rag_access_guard_api.adapters import llm
from rag_access_guard_api.main import create_app
from rag_access_guard_api.schemas.chat import MessageRequest, MessageResponse
from rag_access_guard_api.server import create_event_loop
from rag_access_guard_api.services.model_profiles import DEMO_PROFILE
from tests.support.chat import ChatHttp
from tests.support.chat_generation import ChatCase


def test_offline_start_preserves_history_and_normal_chat_recovers_without_status(
    chat_case: ChatCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    online = [False]
    paths: list[str] = []
    template = TypeAdapter(str).validate_json(
        Path("tests/fixtures/instruct-template.json").read_bytes()
    )

    def respond(request: httpx2.Request) -> httpx2.Response:
        paths.append(request.url.path)
        if not online[0]:
            message = "PRIVATE_OFFLINE"
            raise httpx2.ConnectError(message, request=request)
        if request.url.path == "/api/version":
            return httpx2.Response(200, json={"version": "0.34.4"})
        if request.url.path == "/api/tags":
            return httpx2.Response(
                200,
                json={
                    "models": [
                        {
                            "name": DEMO_PROFILE.manifest.model_name,
                            "digest": DEMO_PROFILE.manifest.model_digest,
                        }
                    ]
                },
            )
        assert request.url.path == "/api/show"
        return httpx2.Response(
            200,
            json={
                "template": template,
                "model_info": {
                    "general.architecture": "qwen3",
                    "qwen3.context_length": 262144,
                    "tokenizer.ggml.model": "gpt2",
                    "tokenizer.ggml.pre": "qwen2",
                    "tokenizer.ggml.add_bos_token": False,
                    "tokenizer.ggml.eos_token_id": 151645,
                },
            },
        )

    def client(url: str) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(respond), base_url=url)

    monkeypatch.setenv("RAG_ACCESS_GUARD_LLM_ADAPTER", "ollama")
    monkeypatch.setenv("RAG_ACCESS_GUARD_MODEL_PROFILE", "qwen3-instruct-demo-v1")
    monkeypatch.setattr(llm, "create_client", client)
    payload = MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="QUESTION")
    with TestClient(
        create_app(),
        base_url="https://rag.test",
        backend_options={"loop_factory": create_event_loop},
    ) as app_client:
        app_client.cookies.update(chat_case.client.cookies)
        assert app_client.get("/api/auth/me").status_code == 200
        assert app_client.get(f"/api/chat/threads/{chat_case.thread}").status_code == 200
        response = app_client.post(
            f"/api/chat/threads/{chat_case.thread}/messages",
            json=payload.model_dump(mode="json"),
            headers=ChatHttp.csrf(app_client),
        )
        assert response.status_code == 503
        assert "PRIVATE_OFFLINE" not in response.text
        with chat_case.database.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM chat_turns")).scalar_one() == 0
            assert connection.execute(text("SELECT revision FROM chat_threads")).scalar_one() == 0
        assert chat_case.model.call_count == 0
        online[0] = True
        response = app_client.post(
            f"/api/chat/threads/{chat_case.thread}/messages",
            json=payload.model_dump(mode="json"),
            headers=ChatHttp.csrf(app_client),
        )
        assert response.status_code == 200
        assert MessageResponse.model_validate_json(response.content).turn.state == "available"
        assert chat_case.model.call_count == 1
        assert paths == ["/api/version", "/api/version", "/api/version", "/api/tags", "/api/show"]
