import json
from hashlib import sha256

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from rag_access_guard_api.schemas.documents import DocumentSummary
from rag_access_guard_api.schemas.ingestion import IngestionManifest
from tests.helpers.docx_factory import DOCX_MIME, paragraph_table_docx
from tests.helpers.pdf_factory import text_pdf
from tests.support.chat import ChatHttp


def test_docx_migration_preserves_old_formats_and_refuses_docx_loss(
    admin_client: TestClient, auth_database: Engine, registered_document: DocumentSummary
) -> None:
    for name, raw, mime in [
        ("old.md", b"# Old Markdown", "text/markdown"),
        ("old.pdf", text_pdf(("Old PDF",)), "application/pdf"),
    ]:
        response = admin_client.post(
            f"/api/admin/documents/{registered_document.id}/versions",
            files={"file": (name, raw, mime)},
            headers=ChatHttp.csrf(admin_client),
        )
        assert response.status_code == 201
    config = Config("apps/api/alembic.ini")
    with auth_database.connect() as connection:
        before = connection.execute(text("SELECT * FROM document_versions ORDER BY id")).all()
        trigger = TypeAdapter(str).validate_python(
            connection.execute(
                text("""SELECT pg_get_triggerdef(oid) FROM pg_trigger
            WHERE tgname='document_versions_immutable'""")
            ).scalar_one()
        )
    command.downgrade(config, "0011_history_closure")
    command.upgrade(config, "head")
    command.check(config)
    with auth_database.connect() as connection:
        assert (
            connection.execute(text("SELECT * FROM document_versions ORDER BY id")).all() == before
        )
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "0013_document_origin"
        )
        assert (
            connection.execute(
                text("""SELECT pg_get_triggerdef(oid) FROM pg_trigger
            WHERE tgname='document_versions_immutable'""")
            ).scalar_one()
            == trigger
        )
    response = admin_client.post(
        f"/api/admin/documents/{registered_document.id}/versions",
        files={"file": ("new.docx", paragraph_table_docx("New", (), ""), DOCX_MIME)},
        headers=ChatHttp.csrf(admin_client),
    )
    assert response.status_code == 201
    with pytest.raises(DBAPIError, match="immutable"), auth_database.begin() as connection:
        _ = connection.execute(text("UPDATE document_versions SET media_type='text/plain'"))
    with auth_database.connect() as connection:
        versions = connection.execute(text("SELECT * FROM document_versions ORDER BY id")).all()
    with pytest.raises(DBAPIError, match="DOCX downgrade requires compatible versions"):
        command.downgrade(config, "0011_history_closure")
    with auth_database.connect() as connection:
        assert (
            connection.execute(text("SELECT * FROM document_versions ORDER BY id")).all()
            == versions
        )


def test_downgrade_refuses_incompatible_mime_width(
    auth_database: Engine, registered_document: DocumentSummary
) -> None:
    with auth_database.begin() as connection:
        _ = connection.execute(text("SET LOCAL session_replication_role=replica"))
        for name in ("media_type", "parser_revision", "manifest"):
            _ = connection.execute(
                text("ALTER TABLE document_versions DROP CONSTRAINT ck_document_versions_" + name)
            )
        _ = connection.execute(text("UPDATE document_versions SET media_type=repeat('x',65)"))
    with pytest.raises(DBAPIError, match="DOCX downgrade requires compatible versions"):
        command.downgrade(Config("apps/api/alembic.ini"), "0011_history_closure")
    with auth_database.connect() as connection:
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "0013_document_origin"
        )
        assert (
            connection.execute(
                text("SELECT media_type FROM document_versions WHERE document_id=:id"),
                {"id": registered_document.id},
            ).scalar_one()
            == "x" * 65
        )


@pytest.mark.parametrize("fault", ["hash", "text-recipe", "parser", "mime", "extra-field"])
def test_docx_sql_tampering_cannot_bypass_closed_manifest(
    admin_client: TestClient, auth_database: Engine, fault: str
) -> None:
    response = admin_client.post(
        "/api/admin/documents",
        data={"title": "Synthetic DOCX"},
        files={"file": ("new.docx", paragraph_table_docx("Text", (), ""), DOCX_MIME)},
        headers=ChatHttp.csrf(admin_client),
    )
    assert response.status_code == 201
    expressions = {
        "hash": "jsonb_set(ingestion_manifest,'{config_sha256}',to_jsonb(repeat('a',64)))",
        "text-recipe": """jsonb_set(ingestion_manifest,'{config_sha256}',
            to_jsonb(CAST(:hash AS text)))""",
        "parser": "jsonb_set(ingestion_manifest,'{parser_revision}','\"utf8-text-v1\"')",
        "mime": "ingestion_manifest",
        "extra-field": "ingestion_manifest || jsonb_build_object('extra',1)",
    }
    with auth_database.connect() as connection:
        manifest = IngestionManifest.model_validate(
            connection.execute(
                text("SELECT ingestion_manifest FROM document_versions")
            ).scalar_one()
        )
    config = json.dumps(
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
    with pytest.raises(IntegrityError), auth_database.begin() as connection:
        _ = connection.execute(
            text(
                """INSERT INTO document_versions
                (id,document_id,original_bytes,content_sha256,extracted_text,text_sha256,
                media_type,byte_size,parser_revision,status,created_by,ingestion_manifest)
                SELECT gen_random_uuid(),document_id,original_bytes,content_sha256,
                extracted_text,text_sha256,"""  # noqa: S608 -- closed test expression whitelist
                + ("'text/plain'" if fault == "mime" else "media_type")
                + ",byte_size,parser_revision,'stored',created_by,"
                + expressions[fault]
                + " FROM document_versions"
            ),
            {"hash": sha256(config.encode()).hexdigest()},
        )
