from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from sqlalchemy import Engine, select, text

from rag_access_guard_api.persistence import PolicyState
from rag_access_guard_api.schemas.access import GrantView
from rag_access_guard_api.schemas.documents import DocumentSummary


def test_removing_one_of_two_roles_keeps_access(
    admin_client: TestClient,
    role_grant: GrantView,
    role_member_id: UUID,
    registered_document: DocumentSummary,
) -> None:
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
    }
    created = admin_client.post(
        "/api/admin/roles", json={"code": "second", "display_name": "Second"}, headers=headers
    )
    assert created.status_code == 201
    second = TypeAdapter(UUID).validate_python(created.json()["id"])
    assert (
        admin_client.put(
            f"/api/admin/roles/{second}/members/{role_member_id}", headers=headers
        ).status_code
        == 204
    )
    granted = admin_client.post(
        f"/api/admin/documents/{role_grant.document_id}/grants",
        json={"role_id": str(second)},
        headers=headers,
    )
    assert granted.status_code == 201
    path = (
        f"/api/documents/{registered_document.id}"
        f"/versions/{registered_document.active_version_id}/text"
    )
    assert (
        admin_client.delete(
            f"/api/admin/roles/{role_grant.role_id}/members/{role_member_id}", headers=headers
        ).status_code
        == 204
    )
    assert admin_client.get(path).status_code == 200
    assert (
        admin_client.delete(
            f"/api/admin/roles/{second}/members/{role_member_id}", headers=headers
        ).status_code
        == 204
    )
    assert admin_client.get(path).status_code == 404
    assert admin_client.get("/api/documents").json() == {"items": []}


def test_revoking_direct_keeps_role_access(
    admin_client: TestClient,
    role_grant: GrantView,
    self_grant: GrantView,
    registered_document: DocumentSummary,
) -> None:
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
    }
    path = (
        f"/api/documents/{registered_document.id}"
        f"/versions/{registered_document.active_version_id}/text"
    )
    grants = f"/api/admin/documents/{registered_document.id}/grants"
    assert admin_client.delete(f"{grants}/{self_grant.id}", headers=headers).status_code == 204
    assert admin_client.get(path).status_code == 200
    assert admin_client.delete(f"{grants}/{role_grant.id}", headers=headers).status_code == 204
    assert admin_client.get(path).status_code == 404


def test_role_grant_audit_failure_rolls_back(
    admin_client: TestClient,
    auth_database: Engine,
    role_id: UUID,
    registered_document: DocumentSummary,
) -> None:
    with auth_database.begin() as connection:
        before = connection.execute(select(PolicyState.revision)).scalar_one()
        _ = connection.execute(
            text("""ALTER TABLE audit_events ADD CONSTRAINT reject_role_grant
            CHECK (event_type <> 'grant_added')""")
        )
    response = admin_client.post(
        f"/api/admin/documents/{registered_document.id}/grants",
        json={"role_id": str(role_id)},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 503
    with auth_database.connect() as connection:
        assert connection.execute(select(PolicyState.revision)).scalar_one() == before
        assert connection.execute(text("SELECT count(*) FROM document_grants")).scalar_one() == 0


def test_unknown_role_grant_is_not_found(
    admin_client: TestClient, registered_document: DocumentSummary
) -> None:
    response = admin_client.post(
        f"/api/admin/documents/{registered_document.id}/grants",
        json={"role_id": str(uuid4())},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 404
