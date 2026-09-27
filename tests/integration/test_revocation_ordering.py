from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Event
from uuid import uuid4

import pytest
from httpx2 import Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from rag_access_guard_api.schemas.chat import MessageRequest
from rag_access_guard_api.services import security
from tests.support.chat import ChatHttp
from tests.support.security_races import RaceHarness
from tests.support.security_sql import policy_transactions


def test_release_waiting_for_revoke_cannot_read_prelock_grants(race_case: RaceHarness) -> None:
    case = race_case.case
    case.model.hold = True
    request = MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="QUESTION")
    with ThreadPoolExecutor(max_workers=2) as pool:
        response = pool.submit(case.send, request)
        try:
            assert case.model.entered.wait(10)
            race_case.hold_mutation = True
            mutation = pool.submit(case.revoke)
            assert race_case.mutation_locked.wait(10)
            assert race_case.mutation_pid is not None
            case.model.resume.set()
            blocked = race_case.wait_for_blocked(race_case.mutation_pid)
        finally:
            race_case.continue_mutation.set()
            case.model.resume.set()
        mutation.result(10)
        result = response.result(10)
    assert race_case.waits == [(blocked, race_case.mutation_pid)]
    assert race_case.releases
    assert not race_case.releases[0].allowed
    assert race_case.releases[0].reason in {"stale_revision", "denied"}
    assert result.turn.state == "neutral"
    assert "SYNTHETIC_ANSWER" not in result.model_dump_json()
    assert case.model.call_count == 1
    assert set(race_case.isolation) == {"read committed"}
    with case.database.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM chat_turns WHERE answer IS NOT NULL")
            ).scalar_one()
            == 0
        )


@pytest.mark.parametrize("surface", ["release", "history", "source", "original"])
def test_each_protected_transaction_locks_policy_before_reading_authority(
    race_case: RaceHarness, surface: str
) -> None:
    case = race_case.case
    saved = case.send(
        MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="FIRST")
    )
    assert saved.turn.state == "available"
    with policy_transactions() as transactions:
        if surface == "release":
            result = case.send(
                MessageRequest(request_id=uuid4(), expected_thread_revision=1, user_input="SECOND")
            )
            assert result.turn.state == "available"
        else:
            path = (
                f"/api/chat/threads/{case.thread}"
                if surface == "history"
                else saved.turn.sources[0].url
            )
            if surface == "original":
                path = path.replace("/content?", "/original?")
            assert case.client.get(path).status_code == 200
    assert transactions
    for statements in transactions:
        assert statements
        assert "FROM policy_state" in statements[0]
        assert "FOR SHARE" in statements[0]
        assert "JOIN" not in statements[0]
        assert all("FOR UPDATE" not in s for s in statements if "FROM policy_state" in s)
        assert any("FROM sessions" in s for s in statements[1:])
    assert set(race_case.isolation) == {"read committed"}


def test_release_finishes_before_waiting_revoke(race_case: RaceHarness) -> None:
    case = race_case.case
    case.model.hold = True
    request = MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="QUESTION")
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(case.send, request)
        try:
            assert case.model.entered.wait(10)
            race_case.hold_gate = True
            case.model.resume.set()
            assert race_case.gate_locked.wait(10)
            assert race_case.gate_pid is not None
            mutation = pool.submit(case.revoke)
            blocked = race_case.wait_for_blocked(race_case.gate_pid)
        finally:
            race_case.continue_gate.set()
            case.model.resume.set()
        result = pending.result(10)
        mutation.result(10)
    assert race_case.waits == [(blocked, race_case.gate_pid)]
    assert race_case.releases[0].allowed
    assert result.turn.answer == case.model.body
    assert case.model.call_count == 1
    assert case.read().turns[0].state == "unavailable"
    assert set(race_case.isolation) == {"read committed"}


@pytest.mark.parametrize("surface", ["history", "source", "original"])
@pytest.mark.parametrize("first", ["mutation", "gate"])
def test_protected_reads_follow_real_policy_lock_order(
    race_case: RaceHarness, surface: str, first: str
) -> None:
    case = race_case.case
    answer = case.send(
        MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="QUESTION")
    )
    assert answer.turn.state == "available"
    path = (
        f"/api/chat/threads/{case.thread}" if surface == "history" else answer.turn.sources[0].url
    )
    if surface == "original":
        path = path.replace("/content?", "/original?")
    marker = case.model.body if surface == "history" else "PROTECTED_SYNTHETIC"
    with ThreadPoolExecutor(max_workers=2) as pool:
        try:
            if first == "mutation":
                race_case.hold_mutation = True
                mutation = pool.submit(case.revoke)
                assert race_case.mutation_locked.wait(10)
                assert race_case.mutation_pid is not None
                reading = pool.submit(case.client.get, path)
                _ = race_case.wait_for_blocked(race_case.mutation_pid)
            else:
                race_case.hold_gate = True
                reading = pool.submit(case.client.get, path)
                assert race_case.gate_locked.wait(10)
                assert race_case.gate_pid is not None
                mutation = pool.submit(case.revoke)
                _ = race_case.wait_for_blocked(race_case.gate_pid)
        finally:
            race_case.continue_gate.set()
            race_case.continue_mutation.set()
        response = reading.result(10)
        mutation.result(10)
    assert len(race_case.waits) == 1
    assert response.headers["cache-control"] == "private, no-store"
    assert (marker in response.text) is (first == "gate")
    assert response.status_code == (404 if first == "mutation" and surface != "history" else 200)
    denied = case.client.get(path)
    assert marker not in denied.text
    assert case.document.title not in denied.text
    assert denied.status_code == (200 if surface == "history" else 404)
    assert set(race_case.isolation) == {"read committed"}


@pytest.mark.parametrize("surface", ["release", "history", "source", "original"])
def test_session_expiry_is_rechecked_after_observed_policy_wait(
    race_case: RaceHarness, monkeypatch: pytest.MonkeyPatch, surface: str
) -> None:
    case = race_case.case
    expired = Event()
    original_clock = security.database_clock

    async def clock(connection: AsyncConnection) -> datetime:
        now = await original_clock(connection)
        return now + timedelta(hours=9) if expired.is_set() else now

    monkeypatch.setattr(security, "database_clock", clock)
    question = MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="QUESTION")
    path = f"/api/chat/threads/{case.thread}"
    if surface != "release":
        saved = case.send(question)
        assert saved.turn.state == "available"
        if surface in {"source", "original"}:
            path = saved.turn.sources[0].url
            if surface == "original":
                path = path.replace("/content?", "/original?")
    releases_before = len(race_case.releases)
    case.model.hold = surface == "release"

    def read() -> Response:
        if surface == "release":
            return case.client.post(
                path + "/messages",
                json=question.model_dump(mode="json"),
                headers=ChatHttp.csrf(case.client),
            )
        return case.client.get(path)

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(read) if surface == "release" else None
        try:
            if surface == "release":
                assert case.model.entered.wait(10)
            race_case.hold_mutation = True
            mutation = pool.submit(
                case.client.post,
                "/api/admin/roles",
                json={"code": "clock_control", "display_name": "Clock control"},
                headers=ChatHttp.csrf(case.client),
            )
            assert race_case.mutation_locked.wait(10)
            assert race_case.mutation_pid is not None
            if pending is None:
                pending = pool.submit(read)
            case.model.resume.set()
            _ = race_case.wait_for_blocked(race_case.mutation_pid)
            expired.set()
        finally:
            race_case.continue_mutation.set()
            case.model.resume.set()
        assert mutation.result(10).status_code == 201
        response = pending.result(10)
    assert response.status_code == (404 if surface in {"source", "original"} else 401)
    assert case.model.body not in response.text
    assert "PROTECTED_SYNTHETIC" not in response.text
    assert len(race_case.releases) == releases_before
