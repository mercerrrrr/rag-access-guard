from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text

from rag_access_guard_api.persistence import PolicyState
from rag_access_guard_api.schemas.access import UserList


def test_duplicate_membership_is_noop(
    admin_client: TestClient, auth_database: Engine, role_id: UUID, role_member_id: UUID
) -> None:
    path = f"/api/admin/roles/{role_id}/members/{role_member_id}"
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
    }
    with auth_database.connect() as connection:
        before = connection.execute(select(PolicyState.revision)).scalar_one()
    assert admin_client.put(path, headers=headers).status_code == 204
    assert admin_client.put(path, headers=headers).status_code == 204
    members = admin_client.get(f"/api/admin/roles/{role_id}/members")
    assert [item.id for item in UserList.model_validate_json(members.content).items] == [
        role_member_id
    ]
    assert members.headers["cache-control"] == "private, no-store"
    with auth_database.connect() as connection:
        assert connection.execute(select(PolicyState.revision)).scalar_one() == before + 1
    assert admin_client.delete(path, headers=headers).status_code == 204
    assert admin_client.delete(path, headers=headers).status_code == 204
    assert admin_client.get(f"/api/admin/roles/{role_id}/members").json() == {"items": []}
    with auth_database.connect() as connection:
        assert connection.execute(select(PolicyState.revision)).scalar_one() == before + 2
        assert connection.execute(
            text("""SELECT event_type, principal_id, role_id
            FROM audit_events WHERE event_type LIKE 'membership_%' ORDER BY policy_revision""")
        ).all() == [
            ("membership_added", role_member_id, role_id),
            ("membership_removed", role_member_id, role_id),
        ]


@pytest.mark.parametrize("present", [True, False])
def test_membership_change_is_atomic_with_revision_and_audit(
    admin_client: TestClient,
    auth_database: Engine,
    role_id: UUID,
    role_member_id: UUID,
    *,
    present: bool,
) -> None:
    path = f"/api/admin/roles/{role_id}/members/{role_member_id}"
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
    }
    if not present:
        assert admin_client.put(path, headers=headers).status_code == 204
    with auth_database.begin() as connection:
        before = connection.execute(select(PolicyState.revision)).scalar_one()
        _ = connection.execute(
            text("""ALTER TABLE audit_events ADD CONSTRAINT reject_membership
            CHECK (event_type NOT IN ('membership_added','membership_removed')) NOT VALID""")
        )
    response = (
        admin_client.put(path, headers=headers)
        if present
        else admin_client.delete(path, headers=headers)
    )
    assert response.status_code == 503
    with auth_database.connect() as connection:
        assert connection.execute(select(PolicyState.revision)).scalar_one() == before
        assert connection.execute(text("SELECT count(*) FROM user_roles")).scalar_one() == (
            0 if present else 1
        )


def test_unknown_role_or_user_is_not_found(
    admin_client: TestClient, role_id: UUID, role_member_id: UUID
) -> None:
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
    }
    for role, user in ((role_id, uuid4()), (uuid4(), role_member_id)):
        path = f"/api/admin/roles/{role}/members/{user}"
        assert admin_client.put(path, headers=headers).status_code == 404
        assert admin_client.delete(path, headers=headers).status_code == 404
    assert admin_client.get(f"/api/admin/roles/{uuid4()}/members").status_code == 404
    assert (
        admin_client.patch(
            f"/api/admin/roles/{uuid4()}", json={"display_name": "Name"}, headers=headers
        ).status_code
        == 404
    )


@pytest.mark.parametrize("valid_origin", [True, False])
def test_role_mutations_require_csrf_and_exact_origin(
    admin_client: TestClient, role_id: UUID, role_member_id: UUID, *, valid_origin: bool
) -> None:
    headers = {"Origin": "https://rag.test" if valid_origin else "https://other.test"}
    if not valid_origin:
        headers["X-CSRF-Token"] = admin_client.cookies["__Host-rag_csrf"]
    member = f"/api/admin/roles/{role_id}/members/{role_member_id}"
    responses = (
        admin_client.post(
            "/api/admin/roles", json={"code": "other", "display_name": "Other"}, headers=headers
        ),
        admin_client.patch(
            f"/api/admin/roles/{role_id}", json={"display_name": "Name"}, headers=headers
        ),
        admin_client.put(member, headers=headers),
        admin_client.delete(member, headers=headers),
    )
    assert all(response.status_code == 403 for response in responses)


def test_role_metadata_audit_failure_rolls_back(
    admin_client: TestClient, auth_database: Engine, role_id: UUID
) -> None:
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
    }
    with auth_database.begin() as connection:
        before = connection.execute(select(PolicyState.revision)).scalar_one()
        _ = connection.execute(
            text("""ALTER TABLE audit_events ADD CONSTRAINT reject_role
            CHECK (event_type <> 'role_changed') NOT VALID""")
        )
    assert (
        admin_client.patch(
            f"/api/admin/roles/{role_id}", json={"display_name": "New"}, headers=headers
        ).status_code
        == 503
    )
    assert (
        admin_client.post(
            "/api/admin/roles", json={"code": "other", "display_name": "Other"}, headers=headers
        ).status_code
        == 503
    )
    with auth_database.connect() as connection:
        assert connection.execute(select(PolicyState.revision)).scalar_one() == before
        assert connection.execute(text("SELECT code, display_name FROM roles")).all() == [
            ("engineering", "Engineering")
        ]
