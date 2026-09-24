from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, Integer, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection

from rag_access_guard_api.persistence import PolicyState, Session, User
from rag_access_guard_api.schemas.access import GrantView
from rag_access_guard_api.schemas.audit import AuditPage
from rag_access_guard_api.schemas.documents import DocumentSummary
from rag_access_guard_api.services import security
from tests.integration.chunk_fixtures import copy_chunked_version
from tests.integration.policy_probe import wait_for_policy_wait


@pytest.mark.parametrize(
    "case",
    [
        ("DELETE FROM document_grants", 404),
        ("UPDATE users SET is_active=false", 401),
        ("UPDATE documents SET is_active=false", 404),
        ("active_version", 404),
        ("UPDATE sessions SET revoked_at=clock_timestamp()", 401),
    ],
)
def test_committed_revocation_prevents_waiting_read(
    admin_client: TestClient,
    auth_database: Engine,
    self_grant: GrantView,
    registered_document: DocumentSummary,
    case: tuple[str, int],
) -> None:
    change, expected = case
    path = (
        f"/api/documents/{self_grant.document_id}"
        f"/versions/{registered_document.active_version_id}/text"
    )
    assert admin_client.get(path).status_code == 200
    with ThreadPoolExecutor(max_workers=1) as executor:
        with auth_database.begin() as writer:
            _ = writer.execute(text("SELECT revision FROM policy_state FOR UPDATE"))
            backend = writer.execute(select(func.pg_backend_pid(type_=Integer))).scalar_one()
            reader = executor.submit(admin_client.get, path)
            wait_for_policy_wait(auth_database, backend)
            if change == "active_version":
                version_id = uuid4()
                assert registered_document.active_version_id is not None
                copy_chunked_version(writer, registered_document.active_version_id, version_id)
                _ = writer.execute(
                    text("UPDATE documents SET active_version_id=:id"), {"id": version_id}
                )
            else:
                _ = writer.execute(text(change))
            _ = writer.execute(update(PolicyState).values(revision=PolicyState.revision + 1))
        response = reader.result(10)
    assert response.status_code == expected
    assert "PROTECTED_SYNTHETIC" not in response.text
    assert response.headers["cache-control"] == "private, no-store"


def test_demoted_admin_cannot_grant_after_wait(
    admin_client: TestClient, auth_database: Engine, registered_document: DocumentSummary
) -> None:
    with auth_database.connect() as connection:
        user_id = connection.execute(select(User.id)).scalar_one()
    with ThreadPoolExecutor(max_workers=1) as executor:
        with auth_database.begin() as writer:
            _ = writer.execute(text("SELECT revision FROM policy_state FOR UPDATE"))
            backend = writer.execute(select(func.pg_backend_pid(type_=Integer))).scalar_one()
            future = executor.submit(
                admin_client.post,
                f"/api/admin/documents/{registered_document.id}/grants",
                json={"user_id": str(user_id)},
                headers={
                    "Origin": "https://rag.test",
                    "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
                },
            )
            wait_for_policy_wait(auth_database, backend)
            _ = writer.execute(update(User).values(is_admin=False))
            _ = writer.execute(update(PolicyState).values(revision=PolicyState.revision + 1))
        assert future.result(10).status_code == 403
    with auth_database.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM document_grants")).scalar_one() == 0


def test_idle_expiry_during_policy_wait_is_denied(
    authenticated_client: TestClient, auth_database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    cutoff = datetime(2026, 9, 23, 12, tzinfo=UTC)
    clock_value = cutoff - timedelta(microseconds=1)

    async def clock(_: AsyncConnection) -> datetime:
        return clock_value

    monkeypatch.setattr(security, "database_clock", clock)
    with auth_database.begin() as connection:
        _ = connection.execute(
            update(Session).values(
                created_at=cutoff - timedelta(hours=1),
                last_seen_at=cutoff - timedelta(minutes=30),
                absolute_expires_at=cutoff + timedelta(hours=1),
            )
        )
    with ThreadPoolExecutor(max_workers=1) as executor:
        with auth_database.begin() as writer:
            _ = writer.execute(text("SELECT revision FROM policy_state FOR UPDATE"))
            backend = writer.execute(select(func.pg_backend_pid(type_=Integer))).scalar_one()
            future = executor.submit(authenticated_client.get, "/api/auth/me")
            wait_for_policy_wait(auth_database, backend)
            clock_value = cutoff
        assert future.result(10).status_code == 401


def test_revision_failure_rolls_back_mutation(
    admin_client: TestClient, auth_database: Engine
) -> None:
    with auth_database.begin() as connection:
        user_id = connection.execute(select(User.id)).scalar_one()
        revision = connection.execute(select(PolicyState.revision)).scalar_one()
        _ = connection.execute(
            text("ALTER TABLE policy_state ADD CONSTRAINT reject_revision CHECK (revision=0)")
        )
    response = admin_client.patch(
        f"/api/admin/users/{user_id}",
        json={"is_active": False},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 503
    with auth_database.connect() as connection:
        assert connection.execute(select(User.is_active)).scalar_one() is True
        assert connection.execute(select(PolicyState.revision)).scalar_one() == revision
        assert (
            connection.execute(
                text("SELECT count(*) FROM audit_events WHERE event_type='user_changed'")
            ).scalar_one()
            == 0
        )


def test_policy_failure_never_returns_previous_content(
    admin_client: TestClient,
    auth_database: Engine,
    self_grant: GrantView,
    registered_document: DocumentSummary,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = (
        f"/api/documents/{self_grant.document_id}"
        f"/versions/{registered_document.active_version_id}/text"
    )
    assert admin_client.get(path).status_code == 200
    with auth_database.begin() as connection:
        _ = connection.execute(text("DROP TABLE policy_state"))
    response = admin_client.get(path)
    assert response.status_code == 503
    assert response.json() == {"detail": "Service unavailable"}
    assert "PROTECTED_SYNTHETIC" not in caplog.text


def test_last_seen_does_not_increment_revision(
    admin_client: TestClient, auth_database: Engine
) -> None:
    with auth_database.connect() as connection:
        before = connection.execute(select(PolicyState.revision)).scalar_one()
    assert admin_client.get("/api/auth/me").status_code == 200
    assert admin_client.get("/api/auth/csrf").status_code == 200
    with auth_database.connect() as connection:
        assert connection.execute(select(PolicyState.revision)).scalar_one() == before


def test_audit_rechecks_admin_after_demotion(
    admin_client: TestClient, auth_database: Engine
) -> None:
    with auth_database.begin() as connection:
        user_id = connection.execute(select(User.id)).scalar_one()
        _ = connection.execute(
            text("""INSERT INTO audit_events (id,event_type,stage,outcome,policy_revision)
            VALUES (:id,'access_checked','read','denied',0)"""),
            {"id": uuid4()},
        )
    page = AuditPage.model_validate_json(
        admin_client.get("/api/admin/audit", params={"limit": 1}).content
    )
    assert page.next_cursor is not None
    assert (
        admin_client.patch(
            f"/api/admin/users/{user_id}",
            json={"is_admin": False},
            headers={
                "Origin": "https://rag.test",
                "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
            },
        ).status_code
        == 200
    )
    response = admin_client.get("/api/admin/audit", params={"cursor": page.next_cursor})
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}
