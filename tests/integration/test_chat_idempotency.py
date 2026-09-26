from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import text

from rag_access_guard_api.schemas.chat import MessageRequest
from tests.support.chat import ChatHttp
from tests.support.chat_generation import ChatCase


def question(revision: int = 0) -> MessageRequest:
    return MessageRequest(
        request_id=uuid4(), expected_thread_revision=revision, user_input="QUESTION"
    )


def test_completed_replay_is_reauthorized_without_generation(chat_case: ChatCase) -> None:
    payload = question()
    first = chat_case.send(payload)
    assert first.thread_revision == 1
    chat_case.revoke()
    replay = chat_case.send(payload)
    assert replay.replayed
    assert replay.turn.state == "unavailable"
    assert replay.turn.answer is None
    assert replay.turn.sources == ()
    assert chat_case.model.call_count == 1


@pytest.mark.parametrize("change", ["payload", "revision", "new_id"])
def test_used_request_and_stale_revision_conflicts(chat_case: ChatCase, change: str) -> None:
    payload = question()
    _ = chat_case.send(payload)
    updates = {
        "payload": {"user_input": "CHANGED"},
        "revision": {"expected_thread_revision": 1},
        "new_id": {"request_id": uuid4()},
    }
    response = chat_case.client.post(
        f"/api/chat/threads/{chat_case.thread}/messages",
        json=payload.model_copy(update=updates[change]).model_dump(mode="json"),
        headers=ChatHttp.csrf(chat_case.client),
    )
    assert response.status_code == 409
    expected = "thread_conflict" if change == "new_id" else "request_conflict"
    assert response.json() == {"detail": expected}
    assert chat_case.model.call_count == 1


@pytest.mark.parametrize("same_request", [True, False])
def test_concurrent_request_cannot_start_second_generation(
    chat_case: ChatCase, *, same_request: bool
) -> None:
    payload = question()
    chat_case.model.hold = True
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(chat_case.send, payload)
        try:
            assert chat_case.model.entered.wait(10)
            response = chat_case.client.post(
                f"/api/chat/threads/{chat_case.thread}/messages",
                json=(payload if same_request else question()).model_dump(mode="json"),
                headers=ChatHttp.csrf(chat_case.client),
            )
            assert response.status_code == 409
            assert response.json() == {"detail": "request_in_progress"}
        finally:
            chat_case.model.resume.set()
        assert running.result(10).turn.state == "available"
    assert chat_case.model.call_count == 1


def test_expired_worker_cannot_overwrite_neutral_replay(chat_case: ChatCase) -> None:
    payload = question()
    chat_case.model.hold = True
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(chat_case.send, payload)
        try:
            assert chat_case.model.entered.wait(10)
            with chat_case.database.begin() as connection:
                _ = connection.execute(
                    text(
                        """UPDATE chat_turns
                        SET lease_expires_at=clock_timestamp()-interval '1 second'"""
                    )
                )
            assert chat_case.read().turns[0].state == "neutral"
            assert chat_case.read().revision == 0
            replay = chat_case.send(payload)
            assert replay.replayed
            assert replay.thread_revision == 1
            assert replay.turn.state == "neutral"
        finally:
            chat_case.model.resume.set()
        assert running.result(10).turn.state == "neutral"
    assert chat_case.read().revision == 1
    assert chat_case.model.call_count == 1


def test_expiry_is_committed_even_when_new_request_revision_conflicts(chat_case: ChatCase) -> None:
    payload = question()
    chat_case.model.hold = True
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(chat_case.send, payload)
        try:
            assert chat_case.model.entered.wait(10)
            with chat_case.database.begin() as connection:
                _ = connection.execute(
                    text(
                        """UPDATE chat_turns
                        SET lease_expires_at=clock_timestamp()-interval '1 second'"""
                    )
                )
            response = chat_case.client.post(
                f"/api/chat/threads/{chat_case.thread}/messages",
                json=question().model_dump(mode="json"),
                headers=ChatHttp.csrf(chat_case.client),
            )
            assert response.status_code == 409
            assert chat_case.read().revision == 1
        finally:
            chat_case.model.resume.set()
        assert running.result(10).turn.state == "neutral"
    chat_case.model.hold = False
    assert chat_case.send(question(1)).thread_revision == 2
