from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import Connection, Engine, event, text
from sqlalchemy.exc import SQLAlchemyError

from rag_access_guard_api.schemas.auth import CsrfResponse
from rag_access_guard_api.schemas.chat import MessageRequest
from rag_access_guard_api.services import chat_completion
from rag_access_guard_api.services.chat_state import NeutralReason, StoredTurn
from rag_access_guard_api.services.chat_turns import ReleasedAnswer, complete_turn
from rag_access_guard_api.services.security import ReadUoW
from tests.support.chat import ChatHttp
from tests.support.chat_generation import ChatCase


def question() -> MessageRequest:
    return MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="QUESTION")


def assert_no_release(case: ChatCase) -> None:
    with case.database.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM chat_turns WHERE answer IS NOT NULL")
            ).scalar_one()
            == 0
        )
        assert connection.execute(text("SELECT count(*) FROM turn_sources")).scalar_one() == 0
        assert (
            connection.execute(
                text("SELECT count(*) FROM audit_events WHERE stage='release'")
            ).scalar_one()
            == 0
        )


def test_logout_login_during_inference_does_not_adopt_old_attempt(chat_case: ChatCase) -> None:
    chat_case.model.hold = True
    old_cookie = chat_case.client.cookies["__Host-rag_session"]
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
            assert chat_case.client.post("/api/auth/logout", headers=headers).status_code == 204
            csrf = CsrfResponse.model_validate_json(
                chat_case.client.get("/api/auth/csrf").content
            ).csrf_token
            logged_in = chat_case.client.post(
                "/api/auth/login",
                json={"login": "reader", "password": "Synthetic-Pass-123"},
                headers={"Origin": "https://rag.test", "X-CSRF-Token": csrf},
            )
            assert logged_in.status_code == 200
            assert chat_case.client.cookies["__Host-rag_session"] != old_cookie
        finally:
            chat_case.model.resume.set()
        response = running.result(10)
    assert response.status_code == 401
    assert chat_case.model.call_count == 1
    assert "SYNTHETIC_ANSWER" not in response.text
    assert chat_case.client.get("/api/auth/me").status_code == 200
    assert chat_case.read().turns[0].state == "pending"
    assert_no_release(chat_case)


def test_commit_failure_never_returns_or_persists_answer(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    reached: list[bool] = []

    async def mark_release(
        uow: ReadUoW, turn: StoredTurn, result: ReleasedAnswer | NeutralReason
    ) -> int:
        revision = await complete_turn(uow, turn, result)
        uow.connection.info["synthetic_release_commit_failure"] = True
        return revision

    def fail_commit(connection: Connection) -> None:
        if connection.info.pop("synthetic_release_commit_failure", False):
            reached.append(True)
            message = "PRIVATE_COMMIT_DETAIL"
            raise SQLAlchemyError(message)

    monkeypatch.setattr(chat_completion, "complete_turn", mark_release)
    event.listen(Engine, "commit", fail_commit)
    try:
        response = chat_case.client.post(
            f"/api/chat/threads/{chat_case.thread}/messages",
            json=question().model_dump(mode="json"),
            headers=ChatHttp.csrf(chat_case.client),
        )
    finally:
        event.remove(Engine, "commit", fail_commit)
    assert reached == [True]
    assert response.status_code == 503
    assert chat_case.model.call_count == 1
    assert "SYNTHETIC_ANSWER" not in response.text
    assert "PRIVATE_COMMIT_DETAIL" not in response.text
    assert "SYNTHETIC_ANSWER" not in caplog.text
    assert_no_release(chat_case)


def test_successful_release_commits_body_sources_and_audit(chat_case: ChatCase) -> None:
    result = chat_case.send(question())
    assert result.turn.state == "available"
    with chat_case.database.connect() as connection:
        assert (
            connection.execute(text("SELECT answer FROM chat_turns")).scalar_one()
            == "SYNTHETIC_ANSWER"
        )
        assert connection.execute(text("SELECT count(*) FROM turn_sources")).scalar_one() == len(
            result.turn.sources
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM audit_events WHERE stage='release'")
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(text("SELECT revision FROM chat_threads")).scalar_one()
            == result.thread_revision
        )
