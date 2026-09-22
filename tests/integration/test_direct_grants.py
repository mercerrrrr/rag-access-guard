from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text

from rag_access_guard_api.persistence import PolicyState, User
from rag_access_guard_api.schemas.access import GrantList, GrantView, UserList
from rag_access_guard_api.schemas.documents import DocumentSummary


def test_grant_user_creates_atomic_permission(
    admin_client: TestClient, auth_database: Engine, registered_document: DocumentSummary
) -> None:
    with auth_database.connect() as connection:
        user_id = connection.execute(select(User.id)).scalar_one()
        revision = connection.execute(select(PolicyState.revision)).scalar_one()
    response = admin_client.post(
        f"/api/admin/documents/{registered_document.id}/grants",
        json={"user_id": str(user_id)},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 201
    grant = GrantView.model_validate_json(response.content)
    assert (grant.user_id, grant.role_id, grant.document_id) == (
        user_id,
        None,
        registered_document.id,
    )
    with auth_database.connect() as connection:
        assert connection.execute(select(PolicyState.revision)).scalar_one() == revision + 1
        assert connection.execute(
            text(
                """SELECT grant_id, policy_revision FROM audit_events
                WHERE event_type = 'grant_added'"""
            )
        ).one() == (grant.id, revision + 1)


def test_duplicate_grant_has_no_revision_side_effect(
    admin_client: TestClient, auth_database: Engine, self_grant: GrantView
) -> None:
    with auth_database.connect() as connection:
        revision = connection.execute(select(PolicyState.revision)).scalar_one()
    response = admin_client.post(
        f"/api/admin/documents/{self_grant.document_id}/grants",
        json={"user_id": str(self_grant.user_id)},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 409
    assert response.json() == {"detail": "Grant already exists"}
    with auth_database.connect() as connection:
        assert connection.execute(select(PolicyState.revision)).scalar_one() == revision
        assert (
            connection.execute(
                text("SELECT count(*) FROM audit_events WHERE event_type = 'grant_added'")
            ).scalar_one()
            == 1
        )


def test_grant_and_audit_rollback_together(
    admin_client: TestClient, auth_database: Engine, registered_document: DocumentSummary
) -> None:
    with auth_database.begin() as connection:
        _ = connection.execute(
            text(
                """ALTER TABLE audit_events ADD CONSTRAINT reject_grant
                CHECK (event_type <> 'grant_added')"""
            )
        )
        user_id = connection.execute(select(User.id)).scalar_one()
        revision = connection.execute(select(PolicyState.revision)).scalar_one()
    response = admin_client.post(
        f"/api/admin/documents/{registered_document.id}/grants",
        json={"user_id": str(user_id)},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 503
    with auth_database.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM document_grants")).scalar_one() == 0
        assert connection.execute(select(PolicyState.revision)).scalar_one() == revision


@pytest.mark.parametrize("field", ["principal_id", "role_id"])
def test_client_principal_id_is_rejected(
    admin_client: TestClient, registered_document: DocumentSummary, field: str
) -> None:
    response = admin_client.post(
        f"/api/admin/documents/{registered_document.id}/grants",
        json={"user_id": str(uuid4()), field: str(uuid4())},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 422


def test_non_admin_cannot_grant(authenticated_client: TestClient) -> None:
    path = f"/api/admin/documents/{uuid4()}/grants"
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": authenticated_client.cookies["__Host-rag_csrf"],
    }
    assert (
        authenticated_client.post(path, json={"user_id": str(uuid4())}, headers=headers).status_code
        == 403
    )
    assert authenticated_client.delete(f"{path}/{uuid4()}", headers=headers).status_code == 403
    assert authenticated_client.get(path).status_code == 403
    assert authenticated_client.get("/api/admin/users").status_code == 403


def test_management_metadata_is_closed(admin_client: TestClient, self_grant: GrantView) -> None:
    users = admin_client.get("/api/admin/users")
    assert users.status_code == 200
    assert len(UserList.model_validate_json(users.content).items) == 1
    grants = admin_client.get(f"/api/admin/documents/{self_grant.document_id}/grants")
    assert GrantList.model_validate_json(grants.content).items == (self_grant,)
    assert users.headers["cache-control"] == grants.headers["cache-control"] == "private, no-store"


def test_revoke_is_document_scoped_and_missing_is_noop(
    admin_client: TestClient, auth_database: Engine, self_grant: GrantView
) -> None:
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
    }
    with auth_database.connect() as connection:
        revision = connection.execute(select(PolicyState.revision)).scalar_one()
    assert (
        admin_client.delete(
            f"/api/admin/documents/{uuid4()}/grants/{self_grant.id}", headers=headers
        ).status_code
        == 404
    )
    path = f"/api/admin/documents/{self_grant.document_id}/grants/{self_grant.id}"
    deleted = admin_client.delete(path, headers=headers)
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert admin_client.delete(path, headers=headers).status_code == 404
    with auth_database.connect() as connection:
        assert connection.execute(select(PolicyState.revision)).scalar_one() == revision + 1
        assert (
            connection.execute(
                text("SELECT count(*) FROM audit_events WHERE event_type = 'grant_removed'")
            ).scalar_one()
            == 1
        )
