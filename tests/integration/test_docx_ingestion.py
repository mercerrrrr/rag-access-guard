from hashlib import sha256

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from sqlalchemy import Engine, text

from rag_access_guard_api.schemas.documents import DocumentSummary, DocumentVersionSummary
from rag_access_guard_api.schemas.ingestion import UploadPayload
from rag_access_guard_api.services import ingestion
from tests.helpers.docx_factory import DOCX_MIME, body_xml, paragraph_table_docx, rewrite_docx
from tests.support.chat import ChatHttp


def test_docx_upload_preserves_canonical_bytes_text_and_manifest(
    admin_client: TestClient, auth_database: Engine, registered_document: DocumentSummary
) -> None:
    raw = paragraph_table_docx("Начало", (("A", "Б"),), "Конец")
    response = admin_client.post(
        f"/api/admin/documents/{registered_document.id}/versions",
        files={"file": ("source.docx", raw, DOCX_MIME)},
        headers=ChatHttp.csrf(admin_client),
    )
    assert response.status_code == 201, response.text
    version = DocumentVersionSummary.model_validate_json(response.content)
    with auth_database.connect() as connection:
        row = connection.execute(
            text(
                """SELECT original_bytes,content_sha256,extracted_text,text_sha256,
                media_type,parser_revision,status FROM document_versions WHERE id=:id"""
            ),
            {"id": version.id},
        ).one()
    canonical = "Начало\n\nA\tБ\n\nКонец"  # noqa: RUF001 -- literal required canonical expectation
    assert row == (
        raw,
        sha256(raw).hexdigest(),
        canonical,
        sha256(canonical.encode()).hexdigest(),
        DOCX_MIME,
        "python-docx-body-v1:1.2.0",
        "ready",
    )


def test_unsupported_docx_does_not_persist_or_activate(
    admin_client: TestClient, auth_database: Engine, registered_document: DocumentSummary
) -> None:
    raw = rewrite_docx(
        paragraph_table_docx("", (), ""),
        {"word/document.xml": body_xml("<w:p><w:r><w:drawing/></w:r></w:p>")},
    )
    with auth_database.connect() as connection:
        before = TypeAdapter(int).validate_python(
            connection.execute(text("SELECT revision FROM policy_state")).scalar_one()
        )
    response = admin_client.post(
        f"/api/admin/documents/{registered_document.id}/versions",
        files={"file": ("source.docx", raw, DOCX_MIME)},
        headers=ChatHttp.csrf(admin_client),
    )
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "unsupported_structure"}}
    with auth_database.connect() as connection:
        assert connection.execute(text("SELECT revision FROM policy_state")).scalar_one() == before
        assert connection.execute(text("SELECT count(*) FROM document_versions")).scalar_one() == 1
        assert (
            connection.execute(text("SELECT active_version_id FROM documents")).scalar_one()
            == registered_document.active_version_id
        )


def test_revocation_during_docx_parser_prevents_activation(
    admin_client: TestClient,
    auth_database: Engine,
    registered_document: DocumentSummary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = ingestion.prepare_upload

    def parse_and_revoke(upload: UploadPayload) -> ingestion.PreparedUpload:
        prepared = original(upload)
        with auth_database.begin() as connection:
            _ = connection.execute(text("SELECT revision FROM policy_state FOR UPDATE NOWAIT"))
            _ = connection.execute(text("UPDATE sessions SET revoked_at=clock_timestamp()"))
        return prepared

    monkeypatch.setattr(ingestion, "prepare_upload", parse_and_revoke)
    response = admin_client.post(
        f"/api/admin/documents/{registered_document.id}/versions",
        files={"file": ("source.docx", paragraph_table_docx("Private", (), ""), DOCX_MIME)},
        headers=ChatHttp.csrf(admin_client),
    )
    assert response.status_code == 401
    with auth_database.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM document_versions")).scalar_one() == 1
