import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text

from rag_access_guard_api.persistence import PolicyState
from rag_access_guard_api.schemas.access import GrantView
from rag_access_guard_api.schemas.documents import DocumentSummary, DocumentVersionSummary


def test_reingestion_creates_new_version_without_mutating_old(
    admin_client: TestClient,
    auth_database: Engine,
    registered_document: DocumentSummary,
    self_grant: GrantView,
) -> None:
    with auth_database.connect() as connection:
        before = connection.execute(text("SELECT * FROM document_versions")).one()
        revision = connection.execute(select(PolicyState.revision)).scalar_one()
    response = admin_client.post(
        f"/api/admin/documents/{registered_document.id}/versions",
        files={"file": ("new.md", b"# Synthetic B", "text/markdown")},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 201
    version = DocumentVersionSummary.model_validate_json(response.content)
    assert version.id != registered_document.active_version_id
    assert "Synthetic B" not in response.text
    with auth_database.connect() as connection:
        assert (
            connection.execute(
                text("SELECT * FROM document_versions WHERE id=:id"),
                {"id": registered_document.active_version_id},
            ).one()
            == before
        )
        assert connection.execute(text("SELECT count(*) FROM document_versions")).scalar_one() == 2
        assert (
            connection.execute(text("SELECT active_version_id FROM documents")).scalar_one()
            == version.id
        )
        assert (
            connection.execute(text("SELECT id FROM document_grants")).scalar_one() == self_grant.id
        )
        assert (
            connection.execute(text("SELECT revision FROM policy_state")).scalar_one()
            == revision + 1
        )
    old_url = (
        f"/api/documents/{registered_document.id}"
        f"/versions/{registered_document.active_version_id}/text"
    )
    assert admin_client.get(old_url).status_code == 404
    assert (
        admin_client.get(
            f"/api/documents/{registered_document.id}/versions/{version.id}/text"
        ).status_code
        == 200
    )


@pytest.mark.parametrize("data", [b"\xff", b"", b"a\x00b"])
def test_parse_failure_preserves_active_version_and_revision(
    admin_client: TestClient,
    auth_database: Engine,
    registered_document: DocumentSummary,
    data: bytes,
) -> None:
    with auth_database.connect() as connection:
        revision = connection.execute(select(PolicyState.revision)).scalar_one()
    response = admin_client.post(
        f"/api/admin/documents/{registered_document.id}/versions",
        files={"file": ("invalid.txt", data, "text/plain")},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid document"}
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["vary"] == "Cookie"
    with auth_database.connect() as connection:
        assert connection.execute(select(PolicyState.revision)).scalar_one() == revision
        assert connection.execute(text("SELECT count(*) FROM document_versions")).scalar_one() == 1
        assert (
            connection.execute(text("SELECT active_version_id FROM documents")).scalar_one()
            == registered_document.active_version_id
        )


def test_identical_upload_creates_new_versions_without_reactivating_document(
    admin_client: TestClient,
    auth_database: Engine,
    registered_document: DocumentSummary,
) -> None:
    with auth_database.begin() as connection:
        _ = connection.execute(text("UPDATE documents SET is_active=false"))
    identifiers = {registered_document.active_version_id}
    for _ in range(2):
        response = admin_client.post(
            f"/api/admin/documents/{registered_document.id}/versions",
            files={"file": ("same.md", b"PROTECTED_SYNTHETIC", "text/plain")},
            headers={
                "Origin": "https://rag.test",
                "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
            },
        )
        assert response.status_code == 201
        identifiers.add(DocumentVersionSummary.model_validate_json(response.content).id)
    assert len(identifiers) == 3
    with auth_database.connect() as connection:
        assert connection.execute(text("SELECT is_active FROM documents")).scalar_one() is False


@pytest.mark.parametrize("missing", ["Origin", "X-CSRF-Token"])
def test_reingestion_requires_origin_and_csrf(
    admin_client: TestClient, registered_document: DocumentSummary, missing: str
) -> None:
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
    }
    del headers[missing]
    response = admin_client.post(
        f"/api/admin/documents/{registered_document.id}/versions",
        files={"file": ("x.txt", b"text", "text/plain")},
        headers=headers,
    )
    assert response.status_code == 403
