from uuid import uuid4

import pytest

from rag_access_guard_api.adapters import llm
from rag_access_guard_api.adapters.model_tokens import get_model_counter
from rag_access_guard_api.schemas.chat import AvailableTurn, MessageRequest
from tests.support.chat_generation import ChatCase


def test_compact_chat_rechecks_stored_answer_and_source_after_revocation(
    chat_case: ChatCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_ACCESS_GUARD_MODEL_PROFILE", "qwen3-instruct-compact-demo-v1")
    counter = get_model_counter()
    monkeypatch.setattr(llm, "get_token_counter", lambda: counter)
    chat_case.model.body = "PROTECTED_COMPACT_ANSWER"
    first = chat_case.send(
        MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="FIRST")
    )
    assert isinstance(first.turn, AvailableTurn)
    source_url = first.turn.sources[0].url
    assert chat_case.client.get(source_url).status_code == 200

    chat_case.revoke()

    stored = chat_case.read().turns[0]
    assert stored.state == "unavailable"
    assert stored.user_input == "FIRST"
    assert stored.answer is None
    assert stored.sources == ()
    denied = chat_case.client.get(source_url)
    assert denied.status_code == 404
    assert denied.headers["cache-control"] == "private, no-store"
    assert "PROTECTED_COMPACT_ANSWER" not in stored.model_dump_json()


def test_compact_chat_does_not_reuse_revoked_history_for_followup(
    chat_case: ChatCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_ACCESS_GUARD_MODEL_PROFILE", "qwen3-instruct-compact-demo-v1")
    counter = get_model_counter()
    monkeypatch.setattr(llm, "get_token_counter", lambda: counter)
    chat_case.model.body = "PROTECTED_COMPACT_ANSWER"
    first = chat_case.send(
        MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="FIRST")
    )
    assert first.turn.state == "available"
    chat_case.revoke()

    followup = chat_case.send(
        MessageRequest(request_id=uuid4(), expected_thread_revision=1, user_input="FOLLOWUP")
    )

    assert followup.turn.state == "neutral"
    assert followup.turn.answer is None
    assert followup.turn.sources == ()
    assert chat_case.model.call_count == 1
