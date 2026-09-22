from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from rag_access_guard_api.persistence import AuthChallenge, Session, User
from rag_access_guard_api.schemas.auth import CsrfResponse
from rag_access_guard_api.services import challenges, security


@pytest.mark.parametrize("offset", [-1, 0, 1])
@pytest.mark.parametrize("boundary", ["idle", "absolute"])
def test_session_clock_boundaries(
    authenticated_client: TestClient,
    auth_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
    offset: int,
    boundary: str,
) -> None:
    cutoff = datetime(2026, 9, 22, 12, tzinfo=UTC)
    with auth_database.begin() as connection:
        _ = connection.execute(
            update(Session).values(
                created_at=cutoff - timedelta(hours=8),
                last_seen_at=cutoff - timedelta(minutes=30 if boundary == "idle" else 1),
                absolute_expires_at=cutoff
                if boundary == "absolute"
                else cutoff + timedelta(hours=1),
            )
        )

    async def clock(_: AsyncConnection) -> datetime:
        return cutoff + timedelta(microseconds=offset)

    monkeypatch.setattr(security, "database_clock", clock)
    response = authenticated_client.get("/api/auth/me")
    assert response.status_code == (200 if offset < 0 else 401)


def test_last_seen_never_extends_absolute_expiry(
    authenticated_client: TestClient, auth_database: Engine
) -> None:
    with auth_database.connect() as connection:
        before = (
            connection.execute(select(Session.absolute_expires_at, Session.last_seen_at))
            .tuples()
            .one()
        )
    assert authenticated_client.get("/api/auth/me").status_code == 200
    with auth_database.connect() as connection:
        after = (
            connection.execute(select(Session.absolute_expires_at, Session.last_seen_at))
            .tuples()
            .one()
        )
    assert after[0] == before[0]
    assert after[1] >= before[1]


def test_inactive_user_fails_every_new_gate(
    authenticated_client: TestClient, auth_database: Engine
) -> None:
    with auth_database.begin() as connection:
        _ = connection.execute(update(User).values(is_active=False))
    assert authenticated_client.get("/api/auth/me").status_code == 401


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_challenge_exact_expiry_boundary(
    auth_client: TestClient, auth_database: Engine, monkeypatch: pytest.MonkeyPatch, offset: int
) -> None:
    csrf = CsrfResponse.model_validate_json(auth_client.get("/api/auth/csrf").content).csrf_token
    with auth_database.connect() as connection:
        expiry = connection.execute(select(AuthChallenge.expires_at)).scalar_one()

    async def clock(_: AsyncConnection) -> datetime:
        return expiry + timedelta(microseconds=offset)

    monkeypatch.setattr(challenges, "database_clock", clock)
    response = auth_client.post(
        "/api/auth/login",
        json={"login": "reader", "password": "Synthetic-Pass-123"},
        headers={"Origin": "https://rag.test", "X-CSRF-Token": csrf},
    )
    assert response.status_code == (200 if offset < 0 else 403)
