from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text

from rag_access_guard_api.persistence import PolicyState
from rag_access_guard_api.schemas.auth import CsrfResponse
from rag_access_guard_api.schemas.documents import (
    DocumentList,
    DocumentSummary,
    DocumentVersionList,
)


@pytest.mark.parametrize("method", ["GET", "POST", "PATCH", "VERSIONS"])
@pytest.mark.parametrize("authenticated", [False, True])
def test_non_admin_cannot_manage_registry(
    auth_client: TestClient, method: str, *, authenticated: bool
) -> None:
    csrf_response = auth_client.get("/api/auth/csrf")
    csrf = CsrfResponse.model_validate_json(csrf_response.content).csrf_token
    if authenticated:
        login = auth_client.post(
            "/api/auth/login",
            json={"login": "reader", "password": "Synthetic-Pass-123"},
            headers={"Origin": "https://rag.test", "X-CSRF-Token": csrf},
        )
        assert login.status_code == 200
        csrf = auth_client.cookies["__Host-rag_csrf"]
    path = "/api/admin/documents"
    if method in {"PATCH", "VERSIONS"}:
        path += f"/{uuid4()}"
    if method == "VERSIONS":
        path += "/versions"
    response = auth_client.request(
        "GET" if method == "VERSIONS" else method,
        path,
        json={"title": "New"} if method == "PATCH" else None,
        headers={"Origin": "https://rag.test", "X-CSRF-Token": csrf},
    )
    assert response.status_code == (403 if authenticated else 401)
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["vary"] == "Cookie"


def test_admin_metadata_contains_no_content(
    admin_client: TestClient, registered_document: DocumentSummary
) -> None:
    listing = admin_client.get("/api/admin/documents")
    assert listing.status_code == 200
    assert DocumentList.model_validate_json(listing.content).items == [registered_document]
    versions = admin_client.get(f"/api/admin/documents/{registered_document.id}/versions")
    parsed = DocumentVersionList.model_validate_json(versions.content)
    assert len(parsed.items) == 1
    assert parsed.items[0].id == registered_document.active_version_id
    assert parsed.items[0].status == "chunked"
    assert "PROTECTED_SYNTHETIC" not in listing.text + versions.text
    assert (
        admin_client.get(
            f"/api/documents/{registered_document.id}/versions/{registered_document.active_version_id}/content"
        ).status_code
        == 404
    )
    assert versions.headers["cache-control"] == "private, no-store"


def test_deactivation_and_noop_revision(
    admin_client: TestClient, auth_database: Engine, registered_document: DocumentSummary
) -> None:
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
    }
    with auth_database.connect() as connection:
        before = connection.execute(select(PolicyState.revision)).scalar_one()
    for _ in range(2):
        response = admin_client.patch(
            f"/api/admin/documents/{registered_document.id}",
            json={"is_active": False},
            headers=headers,
        )
        assert response.status_code == 200
        assert not DocumentSummary.model_validate_json(response.content).is_active
    with auth_database.connect() as connection:
        assert (
            connection.execute(text("SELECT revision FROM policy_state")).scalar_one() == before + 1
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM audit_events WHERE event_type = 'document_changed'")
            ).scalar_one()
            == 2
        )


def test_registry_creation_rolls_back_on_audit_failure(
    admin_client: TestClient, auth_database: Engine
) -> None:
    with auth_database.begin() as connection:
        _ = connection.execute(
            text(
                """ALTER TABLE audit_events ADD CONSTRAINT test_reject_documents
                CHECK (event_type <> 'document_changed')"""
            )
        )
        revision = connection.execute(select(PolicyState.revision)).scalar_one()
    response = admin_client.post(
        "/api/admin/documents",
        data={"title": "Private"},
        files={"file": ("example.txt", b"PROTECTED_SYNTHETIC", "text/plain")},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "Service unavailable"}
    assert response.headers["cache-control"] == "private, no-store"
    with auth_database.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM documents")).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM document_versions")).scalar_one() == 0
        assert (
            connection.execute(text("SELECT revision FROM policy_state")).scalar_one() == revision
        )


@pytest.mark.parametrize("bad", ["missing-csrf", "wrong-csrf", "wrong-origin"])
def test_registry_mutations_require_origin_and_csrf(admin_client: TestClient, bad: str) -> None:
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
    }
    if bad == "missing-csrf":
        del headers["X-CSRF-Token"]
    elif bad == "wrong-csrf":
        headers["X-CSRF-Token"] = "invalid"
    else:
        headers["Origin"] = "https://other.test"
    assert admin_client.post("/api/admin/documents", headers=headers).status_code == 403
    assert (
        admin_client.patch(
            f"/api/admin/documents/{uuid4()}", json={"title": "New"}, headers=headers
        ).status_code
        == 403
    )


def test_unknown_admin_resource_and_empty_patch(
    admin_client: TestClient, registered_document: DocumentSummary
) -> None:
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
    }
    assert admin_client.get(f"/api/admin/documents/{uuid4()}/versions").status_code == 404
    assert (
        admin_client.patch(
            f"/api/admin/documents/{uuid4()}", json={"title": "New"}, headers=headers
        ).status_code
        == 404
    )
    assert (
        admin_client.patch(
            f"/api/admin/documents/{registered_document.id}", json={}, headers=headers
        ).status_code
        == 422
    )


def test_upload_transport_limit_without_content_length(admin_client: TestClient) -> None:
    response = admin_client.post(
        "/api/admin/documents",
        content=iter([b"a" * 6_000_000, b"b" * 6_000_000]),
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
            "Content-Type": "multipart/form-data; boundary=synthetic",
        },
    )
    assert response.status_code == 413
    assert response.headers["cache-control"] == "private, no-store"


def test_malformed_multipart_is_sanitized(admin_client: TestClient) -> None:
    response = admin_client.post(
        "/api/admin/documents",
        content=b"invalid multipart",
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
            "Content-Type": "multipart/form-data; boundary=synthetic",
        },
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid document"}
    assert response.headers["cache-control"] == "private, no-store"
