from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from rag_access_guard import PolicySnapshot, SourceRef
from rag_access_guard_api.adapters.policy import PostgresPolicyReader
from rag_access_guard_api.schemas.chat import MessageRequest
from rag_access_guard_api.services import chat, security
from tests.support.chat import ChatHttp
from tests.support.chat_generation import ChatCase


@pytest.mark.parametrize("boundary", ["session", "lease"])
def test_expiry_during_release_check_prevents_answer_write(
    chat_case: ChatCase,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    # Given: time advances deterministically after source checks, before persistence.
    checked: list[bool] = []
    original_snapshot = PostgresPolicyReader.snapshot
    original_clock = security.database_clock

    async def snapshot(
        self: PostgresPolicyReader,
        principal_id: UUID,
        refs: tuple[SourceRef, ...],
        *,
        thread_id: UUID | None = None,
    ) -> PolicySnapshot:
        result = await original_snapshot(self, principal_id, refs, thread_id=thread_id)
        if thread_id is not None:
            checked.append(True)
        return result

    async def clock(connection: AsyncConnection) -> datetime:
        now = await original_clock(connection)
        return now + timedelta(hours=9) if checked else now

    monkeypatch.setattr(PostgresPolicyReader, "snapshot", snapshot)
    monkeypatch.setattr(security if boundary == "session" else chat, "database_clock", clock)
    # When: an otherwise allowed generation reaches the final gate.
    payload = MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="QUESTION")
    response = chat_case.client.post(
        f"/api/chat/threads/{chat_case.thread}/messages",
        json=payload.model_dump(mode="json"),
        headers=ChatHttp.csrf(chat_case.client),
    )
    # Then: elapsed authority is never used to store or deliver model output.
    assert checked
    assert "SYNTHETIC_ANSWER" not in response.text
    with chat_case.database.connect() as connection:
        assert connection.execute(text("SELECT answer FROM chat_turns")).scalar_one() is None
    assert response.status_code == (401 if boundary == "session" else 200)
