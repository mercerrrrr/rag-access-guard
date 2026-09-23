import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, select, text

from rag_access_guard_api.persistence import PolicyState, User
from rag_access_guard_api.schemas.documents import DocumentSummary
from tests.integration.policy_probe import capture_sql


def test_resource_locks_follow_uuid_order(
    admin_client: TestClient, auth_database: Engine, registered_document: DocumentSummary
) -> None:
    with auth_database.connect() as connection:
        user_id = connection.execute(select(User.id)).scalar_one()
    with capture_sql() as statements:
        response = admin_client.post(
            f"/api/admin/documents/{registered_document.id}/grants",
            json={"user_id": str(user_id)},
            headers={
                "Origin": "https://rag.test",
                "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
            },
        )
    assert response.status_code == 201
    assert "policy_state" in statements[0]
    assert "FOR UPDATE" in statements[0]
    assert all("FOR SHARE" not in statement for statement in statements)
    resources = [
        statement
        for statement in statements
        if statement.startswith("SELECT")
        and "FOR UPDATE" in statement
        and ("FROM documents" in statement or "FROM users" in statement)
    ]
    expected = [
        name for _, name in sorted(((registered_document.id, "documents"), (user_id, "users")))
    ]
    assert len(resources) == 2
    assert all(
        f"FROM {name}" in statement for name, statement in zip(expected, resources, strict=True)
    )


@pytest.mark.parametrize("corruption", ["missing", "multiple", "wrong_id"])
def test_invalid_policy_singleton_is_unavailable(
    admin_client: TestClient, auth_database: Engine, corruption: str
) -> None:
    with auth_database.begin() as connection:
        if corruption == "missing":
            _ = connection.execute(delete(PolicyState))
        else:
            _ = connection.execute(
                text("ALTER TABLE policy_state DROP CONSTRAINT ck_policy_state_singleton")
            )
            _ = connection.execute(text("INSERT INTO policy_state (id, revision) VALUES (2, 0)"))
            if corruption == "wrong_id":
                _ = connection.execute(delete(PolicyState).where(PolicyState.id == 1))
    response = admin_client.get("/api/admin/users")
    assert response.status_code == 503
    assert response.json() == {"detail": "Service unavailable"}
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize("path", ["/api/documents", "/api/admin/audit", "/api/auth/me"])
def test_first_statement_is_policy_lock_without_upgrade(
    admin_client: TestClient, path: str
) -> None:
    with capture_sql() as statements:
        response = admin_client.get(path)
    assert response.status_code == 200
    assert "policy_state" in statements[0]
    assert "FOR SHARE" in statements[0]
    assert all("FOR UPDATE" not in line for line in statements if "policy_state" in line)
