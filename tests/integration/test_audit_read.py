from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, insert

from rag_access_guard_api.persistence import AuditEvent
from rag_access_guard_api.schemas.audit import AuditPage


def test_audit_view_excludes_protected_fields(admin_client: TestClient) -> None:
    response = admin_client.get("/api/admin/audit")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["vary"] == "Cookie"
    page = AuditPage.model_validate_json(response.content)
    entry = page.items[0]
    assert set(entry.model_dump()) == {
        "id",
        "occurred_at",
        "actor_user_id",
        "principal_id",
        "document_id",
        "role_id",
        "grant_id",
        "event_type",
        "stage",
        "outcome",
        "policy_revision",
        "source_count",
    }
    assert entry.document_id is None
    assert entry.event_type == "session_created"
    assert page.next_cursor is None


def test_non_admin_cannot_read_audit(authenticated_client: TestClient) -> None:
    response = authenticated_client.get("/api/admin/audit")
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


def test_audit_filters_and_cursor_have_no_duplicates(
    admin_client: TestClient, auth_database: Engine
) -> None:
    instant = datetime(2025, 1, 1, tzinfo=UTC)
    with auth_database.begin() as connection:
        for number in range(1, 6):
            _ = connection.execute(
                insert(AuditEvent).values(
                    id=UUID(int=number),
                    occurred_at=instant,
                    event_type="access_checked",
                    stage="read",
                    outcome="denied",
                    policy_revision=0,
                )
            )
    query = {
        "event_type": "access_checked",
        "stage": "read",
        "outcome": "denied",
        "limit": "2",
        "since": "2025-01-01T00:00:00Z",
        "until": "2025-01-02T00:00:00Z",
    }
    seen: list[str] = []
    for expected in ([5, 4], [3, 2], [1]):
        response = admin_client.get("/api/admin/audit", params=query)
        assert response.status_code == 200
        page = AuditPage.model_validate_json(response.content)
        ids = [str(item.id) for item in page.items]
        assert ids == [str(UUID(int=number)) for number in expected]
        seen.extend(ids)
        cursor = page.next_cursor
        if cursor is not None:
            query["cursor"] = cursor
    assert len(seen) == len(set(seen)) == 5
    assert cursor is None
    response = admin_client.get(
        "/api/admin/audit",
        params={
            "event_type": "access_checked",
            "until": "2025-01-01T00:00:00Z",
        },
    )
    assert response.json()["items"] == []


@pytest.mark.parametrize(
    "query",
    [
        {"limit": "0"},
        {"limit": "101"},
        {"stage": "arbitrary"},
        {"outcome": "arbitrary"},
        {"event_type": "arbitrary"},
        {"since": "2025-01-01T00:00:00"},
        {"since": "2025-01-02T00:00:00Z", "until": "2025-01-01T00:00:00Z"},
        {"cursor": "***"},
        {"cursor": "bm90LWEtdGltZXN0YW1w"},
    ],
)
def test_invalid_audit_query_is_sanitized(admin_client: TestClient, query: dict[str, str]) -> None:
    response = admin_client.get("/api/admin/audit", params=query)
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid request"}
    assert response.headers["cache-control"] == "private, no-store"
