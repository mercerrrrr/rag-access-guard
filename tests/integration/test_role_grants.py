from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from sqlalchemy import Engine, select, update

from rag_access_guard_api.persistence import PolicyState, User
from rag_access_guard_api.schemas.access import AccessibleDocuments, GrantView
from rag_access_guard_api.schemas.documents import DocumentSummary


def test_removing_role_membership_keeps_direct_grant(
    admin_client: TestClient, self_grant: GrantView, registered_document: DocumentSummary
) -> None:
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
    }
    created = admin_client.post(
        "/api/admin/roles",
        json={"code": "engineering", "display_name": "Engineering"},
        headers=headers,
    )
    assert created.status_code == 201
    role_id = TypeAdapter(UUID).validate_python(created.json()["id"])
    membership = f"/api/admin/roles/{role_id}/members/{self_grant.user_id}"
    assert admin_client.put(membership, headers=headers).status_code == 204
    assert (
        admin_client.post(
            f"/api/admin/documents/{self_grant.document_id}/grants",
            json={"role_id": str(role_id)},
            headers=headers,
        ).status_code
        == 201
    )
    assert admin_client.delete(membership, headers=headers).status_code == 204
    path = (
        f"/api/documents/{self_grant.document_id}"
        f"/versions/{registered_document.active_version_id}/text"
    )
    assert admin_client.get(path).status_code == 200
    assert (
        admin_client.delete(
            f"/api/admin/documents/{self_grant.document_id}/grants/{self_grant.id}",
            headers=headers,
        ).status_code
        == 204
    )
    assert admin_client.get(path).status_code == 404


def test_role_grant_allows_current_member(
    admin_client: TestClient, role_grant: GrantView, registered_document: DocumentSummary
) -> None:
    path = (
        f"/api/documents/{role_grant.document_id}"
        f"/versions/{registered_document.active_version_id}/text"
    )
    assert admin_client.get(path).status_code == 200
    assert (
        len(
            AccessibleDocuments.model_validate_json(
                admin_client.get("/api/documents").content
            ).items
        )
        == 1
    )


def test_duplicate_role_grant_is_conflict(
    admin_client: TestClient, auth_database: Engine, role_grant: GrantView
) -> None:
    with auth_database.connect() as connection:
        before = connection.execute(select(PolicyState.revision)).scalar_one()
    response = admin_client.post(
        f"/api/admin/documents/{role_grant.document_id}/grants",
        json={"role_id": str(role_grant.role_id)},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 409
    with auth_database.connect() as connection:
        assert connection.execute(select(PolicyState.revision)).scalar_one() == before


@pytest.mark.parametrize("both", [True, False])
def test_both_or_neither_grant_subjects_are_rejected(
    admin_client: TestClient, registered_document: DocumentSummary, *, both: bool
) -> None:
    payload = {"user_id": str(uuid4()), "role_id": str(uuid4())} if both else {}
    response = admin_client.post(
        f"/api/admin/documents/{registered_document.id}/grants",
        json=payload,
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 422


def test_inactive_user_with_role_is_denied(
    admin_client: TestClient,
    auth_database: Engine,
    role_grant: GrantView,
    registered_document: DocumentSummary,
) -> None:
    with auth_database.begin() as connection:
        _ = connection.execute(update(User).values(is_active=False))
    path = (
        f"/api/documents/{role_grant.document_id}"
        f"/versions/{registered_document.active_version_id}/text"
    )
    assert admin_client.get(path).status_code == 401
    assert admin_client.get("/api/documents").status_code == 401


def test_role_named_admin_has_no_management_capability(
    admin_client: TestClient, auth_database: Engine, role_member_id: UUID
) -> None:
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
    }
    created = admin_client.post(
        "/api/admin/roles", json={"code": "admin", "display_name": "Administrator"}, headers=headers
    )
    assert created.status_code == 201
    role = TypeAdapter(UUID).validate_python(created.json()["id"])
    assert (
        admin_client.put(
            f"/api/admin/roles/{role}/members/{role_member_id}", headers=headers
        ).status_code
        == 204
    )
    with auth_database.begin() as connection:
        _ = connection.execute(update(User).values(is_admin=False))
    assert admin_client.get("/api/admin/roles").status_code == 403
    assert admin_client.get("/api/admin/users").status_code == 403
    assert admin_client.get(f"/api/admin/roles/{role}/members").status_code == 403


def test_client_cannot_supply_roles_for_read(
    admin_client: TestClient, role_id: UUID, registered_document: DocumentSummary
) -> None:
    path = (
        f"/api/documents/{registered_document.id}"
        f"/versions/{registered_document.active_version_id}/text"
    )
    assert (
        admin_client.get(path, params={"role_id": str(role_id), "roles": str(role_id)}).status_code
        == 404
    )
