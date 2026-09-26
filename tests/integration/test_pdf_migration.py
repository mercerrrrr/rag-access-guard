import json
from hashlib import sha256

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from rag_access_guard_api.schemas.documents import DocumentSummary
from rag_access_guard_api.schemas.ingestion import IngestionManifest
from tests.helpers.pdf_factory import text_pdf


def test_pdf_migration_preserves_text_and_refuses_pdf_data_loss(
    admin_client: TestClient,
    auth_database: Engine,
    registered_document: DocumentSummary,
) -> None:
    config = Config("apps/api/alembic.ini")
    with auth_database.connect() as connection:
        before = connection.execute(text("SELECT * FROM document_versions")).one()
    command.downgrade(config, "0007_chunk_embeddings")
    command.upgrade(config, "head")
    command.check(config)
    with auth_database.connect() as connection:
        assert connection.execute(text("SELECT * FROM document_versions")).one() == before
    response = admin_client.post(
        f"/api/admin/documents/{registered_document.id}/versions",
        files={"file": ("a.pdf", text_pdf(("source",)), "application/pdf")},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 201
    with auth_database.connect() as connection:
        versions = connection.execute(text("SELECT * FROM document_versions ORDER BY id")).all()
    with pytest.raises(DBAPIError, match="PDF downgrade requires compatible versions"):
        command.downgrade(config, "0007_chunk_embeddings")
    with auth_database.connect() as connection:
        assert (
            connection.execute(text("SELECT * FROM document_versions ORDER BY id")).all()
            == versions
        )
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "0010_source_order"
        )


def test_pdf_manifest_cannot_omit_parser_limits(
    admin_client: TestClient,
    auth_database: Engine,
) -> None:
    response = admin_client.post(
        "/api/admin/documents",
        data={"title": "PDF"},
        files={"file": ("a.pdf", text_pdf(("source",)), "application/pdf")},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 201
    with auth_database.connect() as connection:
        manifest = IngestionManifest.model_validate(
            connection.execute(
                text("SELECT ingestion_manifest FROM document_versions")
            ).scalar_one()
        )
    incomplete = json.dumps(
        {
            "parser_revision": manifest.parser_revision,
            "chunker_revision": manifest.chunker_revision,
            "tokenizer_revision": manifest.tokenizer_revision,
            "embedding_model_id": manifest.embedding_model_id,
            "embedding_model_revision": manifest.embedding_model_revision,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    with (
        pytest.raises(IntegrityError, match="ck_document_versions_manifest"),
        auth_database.begin() as connection,
    ):
        _ = connection.execute(
            text("""INSERT INTO document_versions
            (id,document_id,original_bytes,content_sha256,extracted_text,text_sha256,
            media_type,byte_size,parser_revision,status,created_by,ingestion_manifest)
            SELECT gen_random_uuid(),document_id,original_bytes,content_sha256,
            extracted_text,text_sha256,media_type,byte_size,parser_revision,'stored',created_by,
            jsonb_set(ingestion_manifest,'{config_sha256}',to_jsonb(CAST(:hash AS text)))
            FROM document_versions"""),
            {"hash": sha256(incomplete.encode()).hexdigest()},
        )
