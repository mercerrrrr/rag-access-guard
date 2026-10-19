from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from rag_access_guard_api.adapters import embeddings, llm
from rag_access_guard_api.adapters.inference_runtime import InferenceRuntime
from rag_access_guard_api.schemas.chat import MessageRequest
from rag_access_guard_api.services import chat_turns
from rag_access_guard_api.services.assistant_help import HELP_MESSAGE
from rag_access_guard_api.services.audit import AuditRecord
from tests.support.chat import ChatHttp
from tests.support.chat_generation import ChatCase


def unavailable() -> None:
    message = "local help entered inference"
    raise AssertionError(message)


def block_inference(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm, "get_token_counter", unavailable)
    monkeypatch.setattr(llm, "get_llm_adapter", unavailable)
    monkeypatch.setattr(embeddings, "get_embedding_adapter", unavailable)
    monkeypatch.setattr(InferenceRuntime, "try_acquire_generation", unavailable)


def test_help_and_exact_replay_commit_once_without_inference(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    block_inference(monkeypatch)
    request = MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="Привет!")
    first = chat_case.send(request)
    replay = chat_case.send(request)
    assert first.turn.state == "neutral"
    assert first.turn.message == HELP_MESSAGE
    assert first.turn.answer is None
    assert first.turn.sources == ()
    assert first.thread_revision == replay.thread_revision == 1
    assert not first.replayed
    assert replay.replayed
    assert replay.turn == first.turn
    with chat_case.database.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM chat_turns")).scalar_one() == 1
        rows = connection.execute(
            text("""SELECT stage,outcome,source_count FROM audit_events
            WHERE event_type='assistant_help'""")
        ).all()
        assert rows == [("release", "success", 0)]


@pytest.mark.parametrize("change", ["input", "revision", "new_id"])
def test_help_preserves_exact_request_and_revision_conflicts(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    block_inference(monkeypatch)
    request = MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="привет")
    _ = chat_case.send(request)
    updates = {
        "input": {"user_input": "Привет!"},
        "revision": {"expected_thread_revision": 1},
        "new_id": {"request_id": uuid4()},
    }
    response = chat_case.client.post(
        f"/api/chat/threads/{chat_case.thread}/messages",
        json=request.model_copy(update=updates[change]).model_dump(mode="json"),
        headers=ChatHttp.csrf(chat_case.client),
    )
    assert response.status_code == 409
    assert response.json() == {
        "detail": "thread_conflict" if change == "new_id" else "request_conflict"
    }
    assert chat_case.read().revision == 1


@pytest.mark.parametrize("denial", ["foreign", "expired", "csrf"])
def test_help_cannot_bypass_owner_session_or_csrf(
    chat_case: ChatCase, chat_http: ChatHttp, monkeypatch: pytest.MonkeyPatch, denial: str
) -> None:
    block_inference(monkeypatch)
    client = chat_http.other if denial == "foreign" else chat_case.client
    headers = dict(ChatHttp.csrf(client))
    if denial == "expired":
        with chat_case.database.begin() as connection:
            _ = connection.execute(
                text("UPDATE sessions SET absolute_expires_at=clock_timestamp()")
            )
    if denial == "csrf":
        headers["X-CSRF-Token"] = "invalid"
    response = client.post(
        f"/api/chat/threads/{chat_case.thread}/messages",
        json={"request_id": str(uuid4()), "expected_thread_revision": 0, "user_input": "помощь"},
        headers=headers,
    )
    assert response.status_code == {"foreign": 404, "expired": 401, "csrf": 403}[denial]
    with chat_case.database.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM chat_turns")).scalar_one() == 0


def test_help_is_excluded_from_subsequent_model_history(chat_case: ChatCase) -> None:
    _ = chat_case.send(
        MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="помощь")
    )
    result = chat_case.send(
        MessageRequest(request_id=uuid4(), expected_thread_revision=1, user_input="QUESTION")
    )
    assert result.turn.state == "available"
    assert chat_case.model.call_count == 1
    assert "помощь" not in chat_case.model.inputs[0][1]


def test_mixed_greeting_still_uses_rag_preflight(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    def offline() -> None:
        raise llm.LLMUnavailableError

    monkeypatch.setattr(llm, "get_token_counter", offline)
    response = chat_case.client.post(
        f"/api/chat/threads/{chat_case.thread}/messages",
        json={
            "request_id": str(uuid4()),
            "expected_thread_revision": 0,
            "user_input": "Привет, как оформить практику?",
        },
        headers=ChatHttp.csrf(chat_case.client),
    )
    assert response.status_code == 503
    assert chat_case.read().turns == ()


def test_help_audit_failure_rolls_back_turn_and_revision(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failed(_connection: AsyncConnection, _event: AuditRecord, _revision: int) -> None:
        message = "private audit failure"
        raise SQLAlchemyError(message)

    monkeypatch.setattr(chat_turns, "write_audit", failed)
    response = chat_case.client.post(
        f"/api/chat/threads/{chat_case.thread}/messages",
        json={"request_id": str(uuid4()), "expected_thread_revision": 0, "user_input": "привет"},
        headers=ChatHttp.csrf(chat_case.client),
    )
    assert response.status_code == 503
    assert chat_case.read().revision == 0
    assert chat_case.read().turns == ()
