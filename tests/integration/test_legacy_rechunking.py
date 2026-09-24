from uuid import uuid4

import anyio
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from rag_access_guard_api.config import Settings
from rag_access_guard_api.database import create_database_engine
from rag_access_guard_api.schemas.documents import DocumentSummary, DocumentVersionSummary
from rag_access_guard_api.server import create_event_loop
from rag_access_guard_api.services.rechunking import rechunk_version


def test_legacy_rechunk_creates_manifest_without_editing_original(
    registered_document: DocumentSummary,
    auth_database: Engine,
    admin_client: TestClient,
) -> None:
    legacy_id = uuid4()
    with auth_database.begin() as connection:
        _ = connection.execute(
            text("""INSERT INTO document_versions
            (id,document_id,original_bytes,content_sha256,extracted_text,text_sha256,
             media_type,byte_size,parser_revision,status,created_by,ingestion_manifest)
            SELECT :id,document_id,original_bytes,content_sha256,extracted_text,text_sha256,
            media_type,byte_size,parser_revision,'stored',created_by,
            ingestion_manifest || jsonb_build_object(
                'chunker_revision',NULL,'tokenizer_revision',NULL,
                'config_sha256',encode(sha256(convert_to(
                    '{"parser_revision":"utf8-text-v1"}', 'UTF8')),'hex'))
            FROM document_versions WHERE id=:source"""),
            {"id": legacy_id, "source": registered_document.active_version_id},
        )
        before = connection.execute(
            text("SELECT * FROM document_versions WHERE id=:id"), {"id": legacy_id}
        ).one()
    token = admin_client.cookies["__Host-rag_session"]
    csrf = admin_client.cookies["__Host-rag_csrf"]

    async def reprocess() -> DocumentVersionSummary:
        engine = create_database_engine(Settings())
        try:
            return await rechunk_version(
                engine,
                token,
                registered_document.id,
                legacy_id,
                csrf_token=csrf,
            )
        finally:
            await engine.dispose()

    result = anyio.run(reprocess, backend_options={"loop_factory": create_event_loop})
    assert result.id != legacy_id
    assert result.status == "chunked"
    with auth_database.connect() as connection:
        assert (
            connection.execute(
                text("SELECT * FROM document_versions WHERE id=:id"), {"id": legacy_id}
            ).one()
            == before
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM document_chunks WHERE document_version_id=:id"),
                {"id": legacy_id},
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM document_chunks WHERE document_version_id=:id"),
                {"id": result.id},
            ).scalar_one()
            == 1
        )
