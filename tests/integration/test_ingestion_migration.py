from hashlib import sha256
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from rag_access_guard_api.schemas.ingestion import IngestionManifest


def test_legacy_stored_versions_get_truthful_manifest_and_roundtrip(
    isolated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAG_ACCESS_GUARD_DATABASE_URL", isolated_database_url)
    config = Config("apps/api/alembic.ini")
    command.upgrade(config, "0004_document_versions")
    engine = create_engine(isolated_database_url)
    raw = b"\xef\xbb\xbflegacy\r\ntext"
    try:
        with engine.begin() as connection:
            user_id, document_id = uuid4(), uuid4()
            _ = connection.execute(
                text("""INSERT INTO users (id,login,display_name,password_hash)
                VALUES (:id,'legacy','Legacy','synthetic')"""),
                {"id": user_id},
            )
            _ = connection.execute(
                text("""INSERT INTO documents (id,title,created_by)
                VALUES (:id,'Legacy',:user)"""),
                {"id": document_id, "user": user_id},
            )
            _ = connection.execute(
                text("""INSERT INTO document_versions
                (id,document_id,original_bytes,content_sha256,extracted_text,text_sha256,
                media_type,byte_size,parser_revision,status,created_by)
                VALUES (:id,:document,:raw,encode(sha256(:raw),'hex'),:text,
                encode(sha256(convert_to(:text,'UTF8')),'hex'),'text/plain',:size,
                'utf8-text-v1','stored',:user)"""),
                {
                    "id": uuid4(),
                    "document": document_id,
                    "raw": raw,
                    "text": "legacy\ntext",
                    "size": len(raw),
                    "user": user_id,
                },
            )
            before = connection.execute(text("SELECT * FROM document_versions")).one()
        command.upgrade(config, "head")
        with engine.connect() as connection:
            manifest = IngestionManifest.model_validate(
                connection.execute(
                    text("SELECT ingestion_manifest FROM document_versions")
                ).scalar_one()
            )
            assert manifest.source_sha256 == sha256(raw).hexdigest()
            assert manifest.text_sha256 == sha256(b"legacy\ntext").hexdigest()
            assert manifest.byte_size == len(raw)
            assert manifest.chunker_revision is None
        command.downgrade(config, "0004_document_versions")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT * FROM document_versions")).one() == before
        command.upgrade(config, "head")
        command.check(config)
    finally:
        engine.dispose()


def test_downgrade_refuses_new_format_without_data_loss(
    isolated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAG_ACCESS_GUARD_DATABASE_URL", isolated_database_url)
    config = Config("apps/api/alembic.ini")
    command.upgrade(config, "head")
    engine = create_engine(isolated_database_url)
    try:
        with engine.begin() as connection:
            _ = connection.execute(
                text("""INSERT INTO users (id,login,display_name,password_hash)
                VALUES (gen_random_uuid(),'reader','Reader','synthetic')""")
            )
            _ = connection.execute(
                text("""INSERT INTO documents (id,title,created_by)
                SELECT gen_random_uuid(),'Synthetic',id FROM users""")
            )
            _ = connection.execute(
                text("""INSERT INTO document_versions
                (id,document_id,original_bytes,content_sha256,extracted_text,text_sha256,
                 media_type,byte_size,parser_revision,status,created_by,ingestion_manifest)
                SELECT gen_random_uuid(),documents.id,convert_to('x','UTF8'),
                encode(sha256(convert_to('x','UTF8')),'hex'),'x',
                encode(sha256(convert_to('x','UTF8')),'hex'),'text/markdown',1,
                'utf8-text-v1','stored',users.id,
                jsonb_build_object('schema_version',1,
                    'source_sha256',encode(sha256(convert_to('x','UTF8')),'hex'),
                    'text_sha256',encode(sha256(convert_to('x','UTF8')),'hex'),
                    'byte_size',1,'parser_revision','utf8-text-v1','chunker_revision',NULL,
                    'tokenizer_revision',NULL,'embedding_model_id',NULL,
                    'embedding_model_revision',NULL,'config_sha256',
                    encode(sha256(convert_to('{"parser_revision":"utf8-text-v1"}','UTF8')),'hex'))
                FROM documents,users""")
            )
            before = connection.execute(text("SELECT * FROM document_versions")).one()
        with pytest.raises(DBAPIError, match="legacy-compatible"):
            command.downgrade(config, "0004_document_versions")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT * FROM document_versions")).one() == before
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "0005_ingestion_manifest"
            )
    finally:
        engine.dispose()
