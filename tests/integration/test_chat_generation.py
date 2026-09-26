from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from rag_access_guard import PolicySnapshot, SourceRef
from rag_access_guard_api.adapters.policy import PostgresPolicyReader
from rag_access_guard_api.schemas.chat import MessageRequest
from rag_access_guard_api.services import chat
from rag_access_guard_api.services.chat_state import NeutralReason, StoredTurn
from rag_access_guard_api.services.chat_turns import ReleasedAnswer, complete_turn
from rag_access_guard_api.services.security import ReadUoW
from tests.support.chat import ChatHttp
from tests.support.chat_generation import ChatCase


def question() -> MessageRequest:
    return MessageRequest(
        request_id=uuid4(), expected_thread_revision=0, user_input="PASTED_USER_MARKER"
    )


def test_user_input_is_separate_and_answer_sources_are_server_built(chat_case: ChatCase) -> None:
    chat_case.model.body = "<script>UNTRUSTED</script> https://foreign.invalid/source"
    result = chat_case.send(question())
    assert result.turn.state == "available"
    assert result.turn.answer == chat_case.model.body
    assert len(result.turn.sources) == 1
    assert result.turn.sources[0].document_id == chat_case.document.id
    assert result.turn.sources[0].url.startswith("/api/documents/")
    user, system = chat_case.model.inputs[0]
    assert user == "PASTED_USER_MARKER"
    assert "PASTED_USER_MARKER" not in system
    assert "PROTECTED_SYNTHETIC" in system


def test_no_grant_means_no_model_call_even_for_admin(chat_case: ChatCase) -> None:
    chat_case.revoke()
    result = chat_case.send(question())
    assert result.turn.state == "neutral"
    assert result.turn.answer is None
    assert result.turn.sources == ()
    assert chat_case.model.call_count == 0


def test_revoke_commits_during_unlocked_model_and_prevents_release(
    chat_case: ChatCase,
    caplog: pytest.LogCaptureFixture,
) -> None:
    chat_case.model.hold = True
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(chat_case.send, question())
        try:
            assert chat_case.model.entered.wait(10)
            chat_case.revoke()
        finally:
            chat_case.model.resume.set()
        result = running.result(10)
    assert result.turn.state == "neutral"
    assert "SYNTHETIC_ANSWER" not in result.model_dump_json()
    with chat_case.database.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM chat_turns WHERE answer IS NOT NULL")
            ).scalar_one()
            == 0
        )
        assert connection.execute(text("SELECT count(*) FROM turn_sources")).scalar_one() == 0
    assert "SYNTHETIC_ANSWER" not in caplog.text


@pytest.mark.parametrize("mode", ["logout", "absolute", "idle", "inactive"])
def test_invalid_session_during_model_never_stores_output(chat_case: ChatCase, mode: str) -> None:
    chat_case.model.hold = True
    headers = ChatHttp.csrf(chat_case.client)
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(
            chat_case.client.post,
            f"/api/chat/threads/{chat_case.thread}/messages",
            json=question().model_dump(mode="json"),
            headers=headers,
        )
        try:
            assert chat_case.model.entered.wait(10)
            if mode == "logout":
                assert chat_case.client.post("/api/auth/logout", headers=headers).status_code == 204
            else:
                changes = {
                    "absolute": """UPDATE sessions
                    SET created_at=clock_timestamp()-interval '9 hours',
                    last_seen_at=clock_timestamp()-interval '5 seconds',
                    absolute_expires_at=clock_timestamp()-interval '1 second'""",
                    "idle": """UPDATE sessions
                    SET created_at=clock_timestamp()-interval '1 hour',
                    last_seen_at=clock_timestamp()-interval '31 minutes'""",
                    "inactive": "UPDATE users SET is_active=false",
                }
                with chat_case.database.begin() as connection:
                    _ = connection.execute(
                        text("SELECT revision FROM policy_state FOR UPDATE NOWAIT")
                    )
                    _ = connection.execute(text(changes[mode]))
        finally:
            chat_case.model.resume.set()
        response = running.result(10)
    assert response.status_code == 401
    assert "SYNTHETIC_ANSWER" not in response.text
    with chat_case.database.connect() as connection:
        assert connection.execute(text("SELECT answer FROM chat_turns")).scalar_one() is None


def test_model_failure_becomes_server_neutral(chat_case: ChatCase) -> None:
    chat_case.model.fail = True
    result = chat_case.send(question())
    assert result.turn.state == "neutral"
    assert result.thread_revision == 1
    assert result.turn.sources == ()


def test_policy_failure_after_generation_discards_output(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = PostgresPolicyReader.snapshot

    async def fail_release(
        self: PostgresPolicyReader,
        principal_id: UUID,
        refs: tuple[SourceRef, ...],
        *,
        thread_id: UUID | None = None,
    ) -> PolicySnapshot:
        if thread_id is not None:
            message = "PRIVATE_POLICY_FAILURE"
            raise RuntimeError(message)
        return await original(self, principal_id, refs, thread_id=thread_id)

    monkeypatch.setattr(PostgresPolicyReader, "snapshot", fail_release)
    result = chat_case.send(question())
    assert result.turn.state == "neutral"
    assert "PRIVATE_POLICY_FAILURE" not in result.model_dump_json()
    with chat_case.database.connect() as connection:
        assert connection.execute(text("SELECT answer FROM chat_turns")).scalar_one() is None


def test_completion_transaction_failure_rolls_back_answer(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = complete_turn

    async def fail_after_write(
        uow: ReadUoW, turn: StoredTurn, result: ReleasedAnswer | NeutralReason
    ) -> int:
        _ = await original(uow, turn, result)
        message = "PRIVATE_TRANSACTION_FAILURE"
        raise SQLAlchemyError(message)

    monkeypatch.setattr(chat, "complete_turn", fail_after_write)
    response = chat_case.client.post(
        f"/api/chat/threads/{chat_case.thread}/messages",
        json=question().model_dump(mode="json"),
        headers=ChatHttp.csrf(chat_case.client),
    )
    assert response.status_code == 503
    assert "SYNTHETIC_ANSWER" not in response.text
    with chat_case.database.connect() as connection:
        assert connection.execute(text("SELECT answer FROM chat_turns")).scalar_one() is None
        assert connection.execute(text("SELECT count(*) FROM turn_sources")).scalar_one() == 0
        assert (
            connection.execute(
                text("SELECT count(*) FROM audit_events WHERE stage='release'")
            ).scalar_one()
            == 0
        )
