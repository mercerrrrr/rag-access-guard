from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text

from rag_access_guard_api.persistence import PolicyState
from rag_access_guard_api.schemas.access import GrantView
from rag_access_guard_api.schemas.documents import DocumentSummary


@pytest.mark.parametrize("invalid", ["origin", "csrf", "missing_csrf"])
def test_mutations_require_origin_and_csrf(
    admin_client: TestClient, self_grant: GrantView, invalid: str
) -> None:
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
    }
    if invalid == "origin":
        headers["Origin"] = "https://other.test"
    elif invalid == "csrf":
        headers["X-CSRF-Token"] = "invalid"
    else:
        del headers["X-CSRF-Token"]
    path = f"/api/admin/documents/{self_grant.document_id}/grants"
    for response in (
        admin_client.post(path, json={"user_id": str(uuid4())}, headers=headers),
        admin_client.delete(f"{path}/{self_grant.id}", headers=headers),
    ):
        assert response.status_code == 403
        assert response.headers["cache-control"] == "private, no-store"


def test_unknown_subject_or_document_is_not_found(
    admin_client: TestClient, registered_document: DocumentSummary
) -> None:
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
    }
    for document_id in (registered_document.id, uuid4()):
        response = admin_client.post(
            f"/api/admin/documents/{document_id}/grants",
            json={"user_id": str(uuid4())},
            headers=headers,
        )
        assert response.status_code == 404
        assert response.json() == {"detail": "Not found"}
    assert admin_client.get(f"/api/admin/documents/{uuid4()}/grants").status_code == 404


def test_failed_revoke_audit_preserves_permission(
    admin_client: TestClient,
    auth_database: Engine,
    self_grant: GrantView,
    registered_document: DocumentSummary,
) -> None:
    with auth_database.begin() as connection:
        revision = connection.execute(select(PolicyState.revision)).scalar_one()
        _ = connection.execute(
            text("""ALTER TABLE audit_events ADD CONSTRAINT reject_revoke
                CHECK (event_type <> 'grant_removed')""")
        )
    response = admin_client.delete(
        f"/api/admin/documents/{self_grant.document_id}/grants/{self_grant.id}",
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "Service unavailable"}
    with auth_database.connect() as connection:
        assert connection.execute(select(PolicyState.revision)).scalar_one() == revision
    path = (
        f"/api/documents/{self_grant.document_id}"
        f"/versions/{registered_document.active_version_id}/text"
    )
    assert admin_client.get(path).status_code == 200
