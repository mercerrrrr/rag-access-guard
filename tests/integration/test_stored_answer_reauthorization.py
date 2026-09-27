import logging
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from rag_access_guard import PolicySnapshot, SourceRef
from rag_access_guard_api.adapters.policy import PostgresPolicyReader
from rag_access_guard_api.schemas.chat import MessageRequest, ThreadDetail
from tests.support.chat import ChatHttp, seed_pending
from tests.support.stored_chat import StoredChatCase


def test_transitive_ref_revocation_masks_only_dependent_answers(
    stored_chat_case: StoredChatCase,
) -> None:
    stored_chat_case.case.revoke()
    response = stored_chat_case.read()
    assert response.status_code == 200
    detail = ThreadDetail.model_validate_json(response.content)
    assert tuple(turn.state for turn in detail.turns) == (
        "unavailable",
        "unavailable",
        "unavailable",
        "available",
    )
    for turn, request in zip(detail.turns, stored_chat_case.requests, strict=True):
        assert turn.user_input == request.user_input
        if turn.state == "unavailable":
            assert turn.answer is None
            assert turn.sources == ()
    assert detail.turns[3].answer == "ANSWER_D"
    assert stored_chat_case.case.document.title not in response.text


@pytest.mark.parametrize("failed_call", [1, 2, 4])
def test_intermediate_policy_failure_masks_every_protected_answer(
    stored_chat_case: StoredChatCase,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failed_call: int,
) -> None:
    caplog.set_level(logging.DEBUG, logger="rag_access_guard_api")
    original = PostgresPolicyReader.snapshot
    calls = 0

    async def intermittent(
        reader: PostgresPolicyReader,
        principal_id: UUID,
        source_refs: tuple[SourceRef, ...],
        *,
        thread_id: UUID | None = None,
    ) -> PolicySnapshot:
        nonlocal calls
        calls += 1
        if calls == failed_call:
            message = "PRIVATE_POLICY_FAILURE"
            raise ConnectionError(message)
        return await original(reader, principal_id, source_refs, thread_id=thread_id)

    monkeypatch.setattr(PostgresPolicyReader, "snapshot", intermittent)
    response = stored_chat_case.read()
    assert response.status_code == 200
    detail = ThreadDetail.model_validate_json(response.content)
    assert all(turn.state == "unavailable" for turn in detail.turns)
    assert all(turn.answer is None and turn.sources == () for turn in detail.turns)
    assert "PRIVATE_POLICY_FAILURE" not in response.text
    assert "PRIVATE_POLICY_FAILURE" not in caplog.text
    assert "ANSWER_" not in caplog.text
    assert tuple(turn.user_input for turn in detail.turns) == tuple(
        request.user_input for request in stored_chat_case.requests
    )


@pytest.mark.parametrize("conditional", ["If-None-Match", "If-Modified-Since"])
def test_conditional_read_rechecks_access_instead_of_reusing_old_body(
    stored_chat_case: StoredChatCase, conditional: str
) -> None:
    stored_chat_case.case.revoke()
    value = (
        '"old-representation"'
        if conditional == "If-None-Match"
        else "Wed, 01 Jan 2031 00:00:00 GMT"
    )
    response = stored_chat_case.case.client.get(
        f"/api/chat/threads/{stored_chat_case.case.thread}", headers={conditional: value}
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["vary"] == "Cookie"
    for name in "ABC":
        assert f"ANSWER_{name}" not in response.text
    assert "ANSWER_D" in response.text


def test_restored_grant_reveals_original_answers_without_generation_or_rewrite(
    stored_chat_case: StoredChatCase,
) -> None:
    before = stored_chat_case.persisted()
    stored_chat_case.case.revoke()
    denied = ThreadDetail.model_validate_json(stored_chat_case.read().content)
    assert tuple(t.state for t in denied.turns[:3]) == ("unavailable",) * 3
    _ = stored_chat_case.restore()
    restored = ThreadDetail.model_validate_json(stored_chat_case.read().content)
    assert tuple(t.answer for t in restored.turns) == (
        "ANSWER_A",
        "ANSWER_B",
        "ANSWER_C",
        "ANSWER_D",
    )
    assert stored_chat_case.case.model.call_count == 4
    assert stored_chat_case.persisted() == before


def test_completed_replays_share_current_read_mask_without_new_generation(
    stored_chat_case: StoredChatCase,
) -> None:
    stored_chat_case.case.revoke()
    case = stored_chat_case.case
    before = stored_chat_case.persisted()
    for index, request in enumerate(stored_chat_case.requests):
        replay = case.send(request)
        assert replay.replayed
        assert replay.thread_revision == 4
        assert replay.turn.state == ("available" if index == 3 else "unavailable")
        assert replay.turn.answer == ("ANSWER_D" if index == 3 else None)
        assert replay.turn.user_input == request.user_input
    assert case.model.call_count == 4
    assert stored_chat_case.persisted() == before
    conflict = case.client.post(
        f"/api/chat/threads/{case.thread}/messages",
        json=stored_chat_case.requests[0]
        .model_copy(update={"user_input": "CHANGED"})
        .model_dump(mode="json"),
        headers=ChatHttp.csrf(case.client),
    )
    assert conflict.status_code == 409
    assert "ANSWER_" not in conflict.text


def test_policy_outage_preserves_neutral_and_expired_pending_without_mutation(
    stored_chat_case: StoredChatCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = stored_chat_case.case
    case.model.fail = True
    neutral = case.send(
        MessageRequest(
            request_id=uuid4(), expected_thread_revision=4, user_input="NEUTRAL_QUESTION"
        )
    )
    assert neutral.turn.state == "neutral"
    with case.database.begin() as connection:
        pending = seed_pending(connection, case.thread)
        _ = connection.execute(
            text(
                """UPDATE chat_turns SET lease_expires_at=clock_timestamp()-interval '1 second'
                WHERE id=:id"""
            ),
            {"id": pending},
        )
    before = stored_chat_case.persisted()

    async def unavailable(
        _reader: PostgresPolicyReader,
        _principal_id: UUID,
        _source_refs: tuple[SourceRef, ...],
        *,
        thread_id: UUID | None = None,
    ) -> PolicySnapshot:
        del thread_id
        raise ConnectionError

    monkeypatch.setattr(PostgresPolicyReader, "snapshot", unavailable)
    response = stored_chat_case.read()
    assert response.status_code == 200
    detail = ThreadDetail.model_validate_json(response.content)
    assert tuple(t.state for t in detail.turns) == ("unavailable",) * 4 + ("neutral", "neutral")
    assert detail.turns[4] == neutral.turn
    assert detail.turns[5].id == pending
    assert detail.revision == 5
    assert stored_chat_case.persisted() == before
    assert case.model.call_count == 5
