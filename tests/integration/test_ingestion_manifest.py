import json
from hashlib import sha256

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import IntegrityError

from rag_access_guard_api.persistence import DocumentVersion
from rag_access_guard_api.schemas.documents import DocumentSummary
from rag_access_guard_api.schemas.ingestion import IngestionManifest


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
    assert manifest.embedding_model_id is None
    assert manifest.embedding_model_revision is None
    config = json.dumps(
        {
            "parser_revision": "utf8-text-v1",
            "chunker_revision": "e5-window400-overlap50-offsets-v1",
            "tokenizer_revision": manifest.tokenizer_revision,
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
        for status in ("indexing", "ready"):
            _ = connection.execute(
                text("UPDATE document_versions SET status=:status"), {"status": status}
            )
        assert (
            connection.execute(
                text("SELECT ingestion_manifest FROM document_versions")
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
