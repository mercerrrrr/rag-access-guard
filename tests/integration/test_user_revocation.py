from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text

from rag_access_guard_api.persistence import AuditEvent, PolicyState, Session, User
from rag_access_guard_api.services.tokens import issue_token, token_digest


def test_self_deactivation_invalidates_current_session(
    admin_client: TestClient, auth_database: Engine
) -> None:
    with auth_database.connect() as connection:
        user_id = connection.execute(select(User.id)).scalar_one()
        revision = connection.execute(select(PolicyState.revision)).scalar_one()
    response = admin_client.patch(
        f"/api/admin/users/{user_id}",
        json={"is_active": False},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 200
    assert response.json()["is_active"] is False
    assert "password" not in response.text
    assert admin_client.get("/api/auth/me").status_code == 401
    with auth_database.connect() as connection:
        assert connection.execute(select(PolicyState.revision)).scalar_one() == revision + 1
        assert (
            connection.execute(
                select(AuditEvent.principal_id).where(AuditEvent.event_type == "user_changed")
            ).scalar_one()
            == user_id
        )


def test_self_demotion_applies_to_next_admin_request(
    admin_client: TestClient, auth_database: Engine
) -> None:
    with auth_database.connect() as connection:
        user_id = connection.execute(select(User.id)).scalar_one()
    response = admin_client.patch(
        f"/api/admin/users/{user_id}",
        json={"is_admin": False},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 200
    assert admin_client.get("/api/auth/me").status_code == 200
    assert admin_client.get("/api/admin/users").status_code == 403
    assert admin_client.get("/api/admin/audit").status_code == 403


@pytest.mark.parametrize(
    "payload", [{}, {"is_active": None}, {"is_admin": "true"}, {"login": "new"}]
)
def test_invalid_user_patch_is_rejected(
    admin_client: TestClient, payload: dict[str, bool | str | None]
) -> None:
    response = admin_client.patch(
        f"/api/admin/users/{uuid4()}",
        json=payload,
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 422
    assert response.headers["cache-control"] == "private, no-store"


def test_non_admin_cannot_change_users(authenticated_client: TestClient) -> None:
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": authenticated_client.cookies["__Host-rag_csrf"],
    }
    assert (
        authenticated_client.patch(
            f"/api/admin/users/{uuid4()}",
            json={"is_admin": True},
            headers=headers,
        ).status_code
        == 403
    )
    assert (
        authenticated_client.delete(
            f"/api/admin/users/{uuid4()}/sessions/{uuid4()}",
            headers=headers,
        ).status_code
        == 403
    )


@pytest.mark.parametrize("origin", ["https://foreign.test", "https://rag.test"])
def test_user_mutations_require_origin_and_csrf(admin_client: TestClient, origin: str) -> None:
    headers = {"Origin": origin}
    if origin != "https://rag.test":
        headers["X-CSRF-Token"] = admin_client.cookies["__Host-rag_csrf"]
    assert (
        admin_client.patch(
            f"/api/admin/users/{uuid4()}",
            json={"is_active": False},
            headers=headers,
        ).status_code
        == 403
    )
    assert (
        admin_client.delete(
            f"/api/admin/users/{uuid4()}/sessions/{uuid4()}",
            headers=headers,
        ).status_code
        == 403
    )


def test_noop_user_patch_and_repeat_session_revoke_preserve_revision(
    admin_client: TestClient, auth_database: Engine
) -> None:
    session_id = uuid4()
    with auth_database.begin() as connection:
        user_id = connection.execute(select(User.id)).scalar_one()
        revision = connection.execute(select(PolicyState.revision)).scalar_one()
        _ = connection.execute(
            text("""INSERT INTO sessions
            (id,user_id,token_digest,csrf_token_digest,absolute_expires_at)
            VALUES (:id,:user,:token,:csrf,clock_timestamp()+interval '8 hours')"""),
            {
                "id": session_id,
                "user": user_id,
                "token": token_digest(issue_token()),
                "csrf": token_digest(issue_token()),
            },
        )
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
    }
    assert (
        admin_client.patch(
            f"/api/admin/users/{user_id}", json={"is_active": True}, headers=headers
        ).status_code
        == 200
    )
    path = f"/api/admin/users/{user_id}/sessions/{session_id}"
    assert admin_client.delete(path, headers=headers).status_code == 204
    assert admin_client.delete(path, headers=headers).status_code == 204
    with auth_database.connect() as connection:
        assert connection.execute(select(PolicyState.revision)).scalar_one() == revision + 1


def test_user_flag_audit_failure_rolls_back(
    admin_client: TestClient, auth_database: Engine
) -> None:
    with auth_database.begin() as connection:
        user_id = connection.execute(select(User.id)).scalar_one()
        revision = connection.execute(select(PolicyState.revision)).scalar_one()
        _ = connection.execute(
            text(
                """ALTER TABLE audit_events ADD CONSTRAINT reject_user_change
                CHECK (event_type <> 'user_changed')"""
            )
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


def test_admin_session_revoke_is_bound_to_user(
    admin_client: TestClient, auth_database: Engine
) -> None:
    with auth_database.connect() as connection:
        session_id, user_id = connection.execute(select(Session.id, Session.user_id)).tuples().one()
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
    }
    response = admin_client.delete(
        f"/api/admin/users/{uuid4()}/sessions/{session_id}", headers=headers
    )
    assert response.status_code == 404
    assert admin_client.get("/api/auth/me").status_code == 200
    revoked = admin_client.delete(
        f"/api/admin/users/{user_id}/sessions/{session_id}", headers=headers
    )
    assert revoked.status_code == 204
    assert admin_client.get("/api/auth/me").status_code == 401
