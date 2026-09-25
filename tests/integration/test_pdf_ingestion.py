from hashlib import sha256

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text

from rag_access_guard_api.persistence import PolicyState
from rag_access_guard_api.schemas.documents import DocumentSummary, DocumentVersionSummary
from rag_access_guard_api.schemas.ingestion import UploadPayload
from rag_access_guard_api.services import ingestion
from tests.helpers.pdf_factory import image_pdf, text_pdf


def test_pdf_source_hash_matches_original_bytes(
    admin_client: TestClient,
    auth_database: Engine,
    registered_document: DocumentSummary,
) -> None:
    raw = text_pdf(("First", "Second"))
    response = admin_client.post(
        f"/api/admin/documents/{registered_document.id}/versions",
        files={"file": ("source.pdf", raw, "application/pdf")},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 201
    version = DocumentVersionSummary.model_validate_json(response.content)
    assert version.status == "ready"
    with auth_database.connect() as connection:
        row = connection.execute(
            text(
                """SELECT original_bytes,content_sha256,extracted_text,text_sha256,parser_revision
                FROM document_versions WHERE id=:id"""
            ),
            {"id": version.id},
        ).one()
        assert row == (
            raw,
            sha256(raw).hexdigest(),
            "First\n\nSecond",
            sha256(b"First\n\nSecond").hexdigest(),
            "pypdf-plain-v1:6.19.0",
        )


def test_image_only_pdf_is_rejected_without_activation(
    admin_client: TestClient,
    auth_database: Engine,
    registered_document: DocumentSummary,
) -> None:
    with auth_database.connect() as connection:
        before = connection.execute(select(PolicyState.revision)).scalar_one()
    response = admin_client.post(
        f"/api/admin/documents/{registered_document.id}/versions",
        files={"file": ("scan.pdf", image_pdf(), "application/pdf")},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "Text layer required"}
    with auth_database.connect() as connection:
        assert connection.execute(text("SELECT revision FROM policy_state")).scalar_one() == before
        assert connection.execute(text("SELECT count(*) FROM document_versions")).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM document_chunks")).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM chunk_embeddings")).scalar_one() == 1
        assert connection.execute(text("SELECT active_version_id FROM documents")).scalar_one() == (
            registered_document.active_version_id
        )


@pytest.mark.parametrize(
    "data",
    [b"%PDF-broken", text_pdf(("secret",), encrypted=True), text_pdf(("   ",))],
    ids=["broken", "encrypted", "blank"],
)
def test_pdf_parse_failure_keeps_previous_active_version(
    admin_client: TestClient,
    auth_database: Engine,
    registered_document: DocumentSummary,
    data: bytes,
) -> None:
    with auth_database.connect() as connection:
        revision = connection.execute(select(PolicyState.revision)).scalar_one()
    response = admin_client.post(
        f"/api/admin/documents/{registered_document.id}/versions",
        files={"file": ("invalid.pdf", data, "application/pdf")},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid document"}
    assert response.headers["cache-control"] == "private, no-store"
    with auth_database.connect() as connection:
        assert connection.execute(select(PolicyState.revision)).scalar_one() == revision
        assert connection.execute(text("SELECT count(*) FROM document_versions")).scalar_one() == 1
        assert (
            connection.execute(text("SELECT active_version_id FROM documents")).scalar_one()
            == registered_document.active_version_id
        )


def test_session_revoked_during_pdf_parse_blocks_activation(
    admin_client: TestClient,
    auth_database: Engine,
    registered_document: DocumentSummary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = ingestion.prepare_upload

    def parse_and_revoke(upload: UploadPayload) -> ingestion.PreparedUpload:
        result = original(upload)
        with auth_database.begin() as connection:
            _ = connection.execute(text("SELECT revision FROM policy_state FOR UPDATE NOWAIT"))
            _ = connection.execute(text("UPDATE sessions SET revoked_at=clock_timestamp()"))
        return result

    monkeypatch.setattr(ingestion, "prepare_upload", parse_and_revoke)
    response = admin_client.post(
        f"/api/admin/documents/{registered_document.id}/versions",
        files={"file": ("new.pdf", text_pdf(("private text",)), "application/pdf")},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 401
    with auth_database.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM document_versions")).scalar_one() == 1
        assert (
            connection.execute(text("SELECT active_version_id FROM documents")).scalar_one()
            == registered_document.active_version_id
        )
