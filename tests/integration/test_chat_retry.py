from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from rag_access_guard import (
    Guard,
    PolicyReader,
    PolicySnapshot,
    PreparedContext,
    ReleaseDecision,
    SourceRef,
)
from rag_access_guard.types import DenialReason
from rag_access_guard_api.adapters import llm
from rag_access_guard_api.adapters.policy import PostgresPolicyReader
from rag_access_guard_api.persistence import ChatTurn, DocumentGrant
from rag_access_guard_api.schemas.chat import MessageRequest
from rag_access_guard_api.services import chat, security
from tests.integration.test_chat_history_boundaries import add_document
from tests.support.chat import ChatHttp
from tests.support.chat_generation import ChatCase
from tests.support.chat_retry import RetryCase, RetryModel


@pytest.fixture
def retry_case(chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch) -> RetryCase:
    model = RetryModel()
    monkeypatch.setattr(llm, "get_llm_adapter", lambda: model)
    second = add_document(chat_case)
    _ = add_document(chat_case)
    with chat_case.database.connect() as connection:
        second_grant = connection.execute(
            select(DocumentGrant.id).where(
                DocumentGrant.document_id == second.id,
                DocumentGrant.user_id == chat_case.grant.user_id,
            )
        ).scalar_one()
    return RetryCase(
        chat_case, model, ((chat_case.document.id, chat_case.grant.id), (second.id, second_grant))
    )


def test_second_stale_stops_after_exactly_two_model_calls(retry_case: RetryCase) -> None:
    result = retry_case.run_with_revocations(count=2)
    assert len(retry_case.model.inputs) == 2
    assert result.turn.state == "neutral"
    assert result.turn.answer is None
    assert result.turn.sources == ()
    assert retry_case.stored_answers() == ()


def test_one_stale_rebuilds_context_and_stores_only_second_answer(
    retry_case: RetryCase, caplog: pytest.LogCaptureFixture
) -> None:
    result = retry_case.run_with_revocations(count=1)
    assert len(retry_case.model.inputs) == 2
    first, second = retry_case.model.inputs
    assert first[0] == second[0] == "QUESTION"
    assert str(retry_case.chat.document.id) in first[1]
    assert str(retry_case.chat.document.id) not in second[1]
    assert first[1] != second[1]
    assert retry_case.model.answers[0] not in second[1]
    assert result.turn.answer == retry_case.model.answers[1]
    assert result.thread_revision == 1
    assert retry_case.stored_answers() == (retry_case.model.answers[1],)
    assert retry_case.model.answers[0] not in result.model_dump_json()
    assert retry_case.model.answers[0] not in caplog.text


def test_duplicate_during_second_attempt_is_pending_then_reauthorized_replay(
    retry_case: RetryCase,
) -> None:
    case = retry_case.chat
    request = MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="QUESTION")
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(case.send, request)
        try:
            assert retry_case.model.entered[0].wait(10)
            case.revoke()
            retry_case.model.resume[0].set()
            assert retry_case.model.entered[1].wait(10)
            duplicate = case.client.post(
                f"/api/chat/threads/{case.thread}/messages",
                json=request.model_dump(mode="json"),
                headers=ChatHttp.csrf(case.client),
            )
            assert duplicate.status_code == 409
            assert duplicate.json() == {"detail": "request_in_progress"}
        finally:
            for event in retry_case.model.resume:
                event.set()
        result = pending.result(10)
    replay = case.send(request)
    assert replay.replayed
    assert replay.turn == result.turn
    assert replay.thread_revision == result.thread_revision == 1
    assert len(retry_case.model.inputs) == 2
    with case.database.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM chat_turns")).scalar_one() == 1
        assert (
            connection.execute(
                text("SELECT count(*) FROM audit_events WHERE stage='release'")
            ).scalar_one()
            == 1
        )


def test_retry_reloads_history_and_excludes_revoked_prior_pair(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    prior = chat_case.send(
        MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="OLD_QUESTION")
    )
    _ = add_document(chat_case)
    model = RetryModel()
    monkeypatch.setattr(llm, "get_llm_adapter", lambda: model)
    request = MessageRequest(request_id=uuid4(), expected_thread_revision=1, user_input="QUESTION")
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(chat_case.send, request)
        try:
            assert model.entered[0].wait(10)
            assert "OLD_QUESTION" in model.inputs[0][1]
            chat_case.revoke()
        finally:
            for event in model.resume:
                event.set()
        result = pending.result(10)
    assert len(model.inputs) == 2
    assert "OLD_QUESTION" not in model.inputs[1][1]
    assert prior.turn.answer is not None
    assert prior.turn.answer not in model.inputs[1][1]
    assert "PROTECTED_SYNTHETIC" not in model.inputs[1][1]
    assert result.turn.answer == model.answers[1]
    assert result.thread_revision == 2


def test_lease_equality_after_preparation_prevents_model_call(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared: list[bool] = []
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
        if thread_id is None:
            prepared.append(True)
        return result

    async def clock(connection: AsyncConnection) -> datetime:
        if prepared:
            expiry = (await connection.execute(select(ChatTurn.lease_expires_at))).scalar_one()
            assert expiry is not None
            return expiry
        return await original_clock(connection)

    monkeypatch.setattr(PostgresPolicyReader, "snapshot", snapshot)
    monkeypatch.setattr(chat, "database_clock", clock)
    result = chat_case.send(
        MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="QUESTION")
    )
    assert prepared
    assert chat_case.model.call_count == 0
    assert result.turn.state == "neutral"
    assert result.turn.answer is None


@pytest.mark.parametrize("reason", ["denied", "invalid_provenance", "policy_unavailable"])
def test_non_stale_release_denials_never_retry(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch, reason: DenialReason
) -> None:
    async def denied(
        self: Guard,
        principal_id: UUID,
        thread_id: UUID,
        prepared: PreparedContext,
        policy_reader: PolicyReader,
    ) -> ReleaseDecision:
        del self, principal_id, thread_id, policy_reader
        return ReleaseDecision(
            allowed=False, reason=reason, policy_revision=prepared.policy_revision
        )

    monkeypatch.setattr(Guard, "authorize_release", denied)
    result = chat_case.send(
        MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="QUESTION")
    )
    assert result.turn.state == "neutral"
    assert result.turn.answer is None
    assert chat_case.model.call_count == 1


def test_lease_expiring_during_retry_preparation_prevents_second_model_call(
    retry_case: RetryCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshots: list[int] = []
    original = PostgresPolicyReader.snapshot
    original_clock = security.database_clock

    async def snapshot(
        self: PostgresPolicyReader,
        principal_id: UUID,
        refs: tuple[SourceRef, ...],
        *,
        thread_id: UUID | None = None,
    ) -> PolicySnapshot:
        result = await original(self, principal_id, refs, thread_id=thread_id)
        if thread_id is None:
            snapshots.append(result.revision)
        return result

    async def clock(connection: AsyncConnection) -> datetime:
        if len(snapshots) >= 2:
            expiry = (await connection.execute(select(ChatTurn.lease_expires_at))).scalar_one()
            assert expiry is not None
            return expiry
        return await original_clock(connection)

    monkeypatch.setattr(PostgresPolicyReader, "snapshot", snapshot)
    monkeypatch.setattr(chat, "database_clock", clock)
    result = retry_case.run_with_revocations(count=1)
    assert len(snapshots) == 2
    assert snapshots[1] > snapshots[0]
    assert len(retry_case.model.inputs) == 1
    assert result.turn.state == "neutral"
    assert result.turn.answer is None
    assert retry_case.stored_answers() == ()
