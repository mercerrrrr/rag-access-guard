from dataclasses import dataclass
from pathlib import Path
from threading import Event
from uuid import UUID

import pytest
from anyio.to_thread import run_sync
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from tests.integration.search_fixtures import configure_search
from tests.support.chat import ChatHttp

from rag_access_guard_api.adapters import llm
from rag_access_guard_api.schemas.access import GrantView
from rag_access_guard_api.schemas.chat import (
    MessageRequest,
    MessageResponse,
    ThreadDetail,
    ThreadView,
)
from rag_access_guard_api.schemas.documents import DocumentSummary


class CapturingLLM:
    """A controllable model boundary; storage and policy remain real."""

    def __init__(self) -> None:
        self.inputs: list[tuple[str, str]] = []
        self.entered: Event = Event()
        self.resume: Event = Event()
        self.hold: bool = False
        self.fail: bool = False
        self.body: str = "SYNTHETIC_ANSWER"

    @property
    def call_count(self) -> int:
        return len(self.inputs)

    async def generate(self, *, user_input: str, system_supplied_context: str) -> str:
        self.inputs.append((user_input, system_supplied_context))
        self.entered.set()
        if self.hold and not await run_sync(self.resume.wait, 20):
            raise TimeoutError
        if self.fail:
            raise llm.LLMUnavailableError
        return self.body


@dataclass(frozen=True, slots=True)
class ChatCase:
    client: TestClient
    database: Engine
    document: DocumentSummary
    grant: GrantView
    thread: UUID
    model: CapturingLLM

    def send(self, payload: MessageRequest) -> MessageResponse:
        response = self.client.post(
            f"/api/chat/threads/{self.thread}/messages",
            json=payload.model_dump(mode="json"),
            headers=ChatHttp.csrf(self.client),
        )
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "private, no-store"
        return MessageResponse.model_validate_json(response.content)

    def revoke(self) -> None:
        response = self.client.delete(
            f"/api/admin/documents/{self.document.id}/grants/{self.grant.id}",
            headers=ChatHttp.csrf(self.client),
        )
        assert response.status_code == 204

    def read(self) -> ThreadDetail:
        response = self.client.get(f"/api/chat/threads/{self.thread}")
        assert response.status_code == 200
        return ThreadDetail.model_validate_json(response.content)


@pytest.fixture
def chat_case(  # noqa: PLR0913, PLR0917 -- distinct existing fixture resources.
    admin_client: TestClient,
    auth_database: Engine,
    registered_document: DocumentSummary,
    self_grant: GrantView,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> ChatCase:
    configure_search(tmp_path / "calibration.json", monkeypatch)
    model = CapturingLLM()
    monkeypatch.setattr(llm, "get_llm_adapter", lambda: model)
    response = admin_client.post("/api/chat/threads", json={}, headers=ChatHttp.csrf(admin_client))
    assert response.status_code == 201
    thread = ThreadView.model_validate_json(response.content).id
    return ChatCase(admin_client, auth_database, registered_document, self_grant, thread, model)
