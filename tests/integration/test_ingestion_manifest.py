import json
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import IntegrityError

from rag_access_guard_api.persistence import DocumentVersion
from rag_access_guard_api.schemas.documents import DocumentSummary
from rag_access_guard_api.schemas.ingestion import IngestionManifest
from tests.integration.chunk_fixtures import copy_chunk_set, copy_stored_version


def test_manifest_binds_source_and_extracted_text(
    registered_document: DocumentSummary, auth_database: Engine
) -> None:
    assert registered_document.active_version_id is not None
    with auth_database.connect() as connection:
        raw = connection.execute(select(DocumentVersion.ingestion_manifest)).scalar_one()
    manifest = IngestionManifest.model_validate(raw)
    assert manifest.source_sha256 == sha256(b"PROTECTED_SYNTHETIC").hexdigest()
    assert manifest.text_sha256 == manifest.source_sha256
    assert manifest.byte_size == len(b"PROTECTED_SYNTHETIC")
    assert manifest.chunker_revision == "e5-window400-overlap50-offsets-v1"
    assert manifest.tokenizer_revision == (
        "intfloat/multilingual-e5-small@614241f622f53c4eeff9890bdc4f31cfecc418b3:content-no-special"
    )
    assert manifest.embedding_model_id == "intfloat/multilingual-e5-small"
    assert manifest.embedding_model_revision == "614241f622f53c4eeff9890bdc4f31cfecc418b3"
    config = json.dumps(
        {
            "parser_revision": "utf8-text-v1",
            "chunker_revision": "e5-window400-overlap50-offsets-v1",
            "tokenizer_revision": manifest.tokenizer_revision,
            "embedding_model_id": manifest.embedding_model_id,
            "embedding_model_revision": manifest.embedding_model_revision,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    assert manifest.config_sha256 == sha256(config.encode()).hexdigest()


def test_manifest_update_is_rejected(
    registered_document: DocumentSummary, auth_database: Engine
) -> None:
    assert registered_document.active_version_id is not None
    with auth_database.begin() as connection, pytest.raises(IntegrityError, match="immutable"):
        _ = connection.execute(
            text("UPDATE document_versions SET ingestion_manifest=ingestion_manifest")
        )


def test_lifecycle_preserves_manifest(
    registered_document: DocumentSummary, auth_database: Engine
) -> None:
    assert registered_document.active_version_id is not None
    with auth_database.begin() as connection:
        before = connection.execute(select(DocumentVersion.ingestion_manifest)).scalar_one()
        target = uuid4()
        copy_stored_version(connection, registered_document.active_version_id, target)
        copy_chunk_set(connection, registered_document.active_version_id, target)
        for status in ("chunked", "indexing"):
            _ = connection.execute(
                text("UPDATE document_versions SET status=:status WHERE id=:id"),
                {"status": status, "id": target},
            )
        _ = connection.execute(
            text("""INSERT INTO chunk_embeddings(chunk_id,embedding,model_id,model_revision)
            SELECT c.id,e.embedding,e.model_id,e.model_revision
            FROM document_chunks c JOIN document_chunks original
              ON original.ordinal=c.ordinal AND original.document_version_id=:source
            JOIN chunk_embeddings e ON e.chunk_id=original.id
            WHERE c.document_version_id=:target"""),
            {"source": registered_document.active_version_id, "target": target},
        )
        _ = connection.execute(
            text("UPDATE document_versions SET status='ready' WHERE id=:id"), {"id": target}
        )
        assert (
            connection.execute(
                text("SELECT ingestion_manifest FROM document_versions WHERE id=:id"),
                {"id": target},
            ).scalar_one()
            == before
        )
    with auth_database.begin() as connection, pytest.raises(IntegrityError):
        _ = connection.execute(text("UPDATE document_versions SET status='stored'"))


@pytest.mark.parametrize("status", ["ready", "stored", "chunked", "failed"])
def test_invalid_transition_is_rejected(
    registered_document: DocumentSummary, auth_database: Engine, status: str
) -> None:
    assert registered_document.active_version_id is not None
    with auth_database.begin() as connection, pytest.raises(IntegrityError):
        _ = connection.execute(
            text("UPDATE document_versions SET status=:status"), {"status": status}
        )
