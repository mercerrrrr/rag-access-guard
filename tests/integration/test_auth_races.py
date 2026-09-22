from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from time import monotonic

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection

from rag_access_guard_api.persistence import Session, User
from rag_access_guard_api.schemas.auth import CsrfResponse
from rag_access_guard_api.services import passwords, security


def test_password_verification_holds_no_policy_lock(
    auth_client: TestClient, auth_database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = Event(), Event()
    original = passwords.verify_password

    def verify(password: str, encoded: str) -> bool:
        entered.set()
        assert release.wait(timeout=10)
        return original(password, encoded)

    monkeypatch.setattr(passwords, "verify_password", verify)
    csrf = CsrfResponse.model_validate_json(auth_client.get("/api/auth/csrf").content).csrf_token
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            auth_client.post,
            "/api/auth/login",
            json={"login": "reader", "password": "Synthetic-Pass-123"},
            headers={"Origin": "https://rag.test", "X-CSRF-Token": csrf},
        )
        try:
            assert entered.wait(timeout=10)
            with auth_database.begin() as connection:
                _ = connection.execute(text("SET LOCAL lock_timeout = '500ms'"))
                _ = connection.execute(select(User.id).with_for_update())
                _ = connection.execute(
                    text("SELECT revision FROM policy_state WHERE id=1 FOR UPDATE")
                )
        finally:
            release.set()
        assert future.result(timeout=10).status_code == 200


@pytest.mark.parametrize("change", ["password", "active"])
def test_password_or_active_change_during_verify_prevents_session(
    auth_client: TestClient, auth_database: Engine, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    entered, release = Event(), Event()
    original = passwords.verify_password
    replacement = passwords.hash_password("Different-Synthetic-Pass")

    def verify(password: str, encoded: str) -> bool:
        entered.set()
        assert release.wait(timeout=10)
        return original(password, encoded)

    monkeypatch.setattr(passwords, "verify_password", verify)
    csrf = CsrfResponse.model_validate_json(auth_client.get("/api/auth/csrf").content).csrf_token
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            auth_client.post,
            "/api/auth/login",
            json={"login": "reader", "password": "Synthetic-Pass-123"},
            headers={"Origin": "https://rag.test", "X-CSRF-Token": csrf},
        )
        try:
            assert entered.wait(timeout=10)
            with auth_database.begin() as connection:
                _ = connection.execute(text("SET LOCAL lock_timeout = '500ms'"))
                _ = connection.execute(
                    text("SELECT revision FROM policy_state WHERE id=1 FOR UPDATE")
                )
                statement = (
                    update(User).values(password_hash=replacement)
                    if change == "password"
                    else update(User).values(is_active=False)
                )
                _ = connection.execute(statement)
        finally:
            release.set()
        response = future.result(timeout=10)
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid credentials"}
    with auth_database.connect() as connection:
        assert connection.execute(select(func.count()).select_from(Session)).scalar_one() == 0


@pytest.mark.parametrize("lock_target", ["policy", "session"])
def test_clock_is_read_after_security_lock(
    authenticated_client: TestClient,
    auth_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
    lock_target: str,
) -> None:
    cutoff = datetime(2026, 9, 22, 12, tzinfo=UTC)
    clock_value = cutoff - timedelta(microseconds=1)

    async def clock(_: AsyncConnection) -> datetime:
        return clock_value

    monkeypatch.setattr(security, "database_clock", clock)
    with auth_database.begin() as connection:
        _ = connection.execute(
            update(Session).values(
                created_at=cutoff - timedelta(hours=8),
                last_seen_at=cutoff - timedelta(minutes=1),
                absolute_expires_at=cutoff,
            )
        )
    with ThreadPoolExecutor(max_workers=1) as executor:
        with auth_database.begin() as holder:
            statement = (
                "SELECT revision FROM policy_state WHERE id=1 FOR UPDATE"
                if lock_target == "policy"
                else "SELECT id FROM sessions FOR UPDATE"
            )
            _ = holder.execute(text(statement))
            future = executor.submit(authenticated_client.get, "/api/auth/me")
            deadline = monotonic() + 10
            with auth_database.connect() as observer:
                while True:
                    if observer.execute(
                        text("""SELECT count(*) FROM pg_stat_activity
                    WHERE datname = current_database() AND wait_event_type='Lock'
                    AND pid <> pg_backend_pid()""")
                    ).scalar_one():
                        break
                    assert monotonic() < deadline
                    observer.rollback()
            clock_value = cutoff
        assert future.result(timeout=10).status_code == 401
