from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text

from rag_access_guard_api.persistence import PolicyState


def test_role_creation_normalizes_name_and_records_change(
    admin_client: TestClient, auth_database: Engine
) -> None:
    with auth_database.connect() as connection:
        revision = connection.execute(select(PolicyState.revision)).scalar_one()
    response = admin_client.post(
        "/api/admin/roles",
        json={"code": "engineering", "display_name": " Engineering "},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 201
    assert response.json()["display_name"] == "Engineering"
    assert admin_client.get("/api/admin/roles").json() == {"items": [response.json()]}
    with auth_database.connect() as connection:
        assert connection.execute(select(PolicyState.revision)).scalar_one() == revision + 1
        assert (
            connection.execute(
                text("SELECT count(*) FROM audit_events WHERE event_type='role_changed'")
            ).scalar_one()
            == 1
        )


@pytest.mark.parametrize("code", ["", "Admin", "a-b", "a" * 65, "a\n"])
def test_role_code_is_validated(admin_client: TestClient, code: str) -> None:
    response = admin_client.post(
        "/api/admin/roles",
        json={"code": code, "display_name": "Name"},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 422
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize("name", [" ", "x" * 201, "bad\x00name"])
def test_role_name_is_validated(admin_client: TestClient, name: str) -> None:
    response = admin_client.post(
        "/api/admin/roles",
        json={"code": "engineering", "display_name": name},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 422


def test_duplicate_role_and_identical_name_do_not_change_revision(
    admin_client: TestClient, auth_database: Engine, role_id: UUID
) -> None:
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
    }
    with auth_database.connect() as connection:
        revision = connection.execute(select(PolicyState.revision)).scalar_one()
    assert (
        admin_client.post(
            "/api/admin/roles",
            json={"code": "engineering", "display_name": "Different"},
            headers=headers,
        ).status_code
        == 409
    )
    assert (
        admin_client.patch(
            f"/api/admin/roles/{role_id}", json={"display_name": " Engineering "}, headers=headers
        ).status_code
        == 200
    )
    with auth_database.connect() as connection:
        assert connection.execute(select(PolicyState.revision)).scalar_one() == revision
    changed = admin_client.patch(
        f"/api/admin/roles/{role_id}", json={"display_name": "New name"}, headers=headers
    )
    assert changed.status_code == 200
    assert changed.json()["display_name"] == "New name"
    with auth_database.connect() as connection:
        assert connection.execute(select(PolicyState.revision)).scalar_one() == revision + 1


@pytest.mark.parametrize("field", ["code", "id", "permissions"])
def test_role_code_cannot_be_changed(admin_client: TestClient, role_id: UUID, field: str) -> None:
    response = admin_client.patch(
        f"/api/admin/roles/{role_id}",
        json={"display_name": "New", field: "admin"},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 422


def test_only_admin_can_manage_roles(authenticated_client: TestClient) -> None:
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": authenticated_client.cookies["__Host-rag_csrf"],
    }
    role = f"/api/admin/roles/{uuid4()}"
    member = f"{role}/members/{uuid4()}"
    responses = (
        authenticated_client.get("/api/admin/roles"),
        authenticated_client.post(
            "/api/admin/roles", json={"code": "valid", "display_name": "Valid"}, headers=headers
        ),
        authenticated_client.patch(role, json={"display_name": "Valid"}, headers=headers),
        authenticated_client.get(f"{role}/members"),
        authenticated_client.put(member, headers=headers),
        authenticated_client.delete(member, headers=headers),
    )
    assert all(response.status_code == 403 for response in responses)
    assert all(response.headers["cache-control"] == "private, no-store" for response in responses)
