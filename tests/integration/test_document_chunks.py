from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import IntegrityError

from rag_access_guard_api.adapters.tokenizer import E5TokenCounter
from rag_access_guard_api.persistence import PolicyState
from rag_access_guard_api.schemas.documents import DocumentSummary
from rag_access_guard_api.services import ingestion
from rag_access_guard_api.services.chunking import ChunkDraft, chunk_text
from tests.integration.chunk_fixtures import copy_chunk_set, copy_stored_version


def test_ingestion_activates_only_complete_chunked_version(
    registered_document: DocumentSummary, auth_database: Engine, admin_client: TestClient
) -> None:
    response = admin_client.get(f"/api/admin/documents/{registered_document.id}/versions")
    assert response.status_code == 200
    with auth_database.connect() as connection:
        assert (
            connection.execute(text("SELECT status FROM document_versions")).scalar_one()
            == "chunked"
        )
        assert connection.execute(text("SELECT count(*) FROM document_chunks")).scalar_one() == 1


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE document_chunks SET text=text",
        "UPDATE document_chunks SET char_start=char_start",
        "UPDATE document_chunks SET document_id=document_id",
        "DELETE FROM document_chunks",
    ],
)
def test_chunk_mutation_is_rejected(
    registered_document: DocumentSummary,
    auth_database: Engine,
    statement: str,
) -> None:
    assert registered_document.active_version_id is not None
    with auth_database.begin() as connection, pytest.raises(IntegrityError, match="immutable"):
        _ = connection.execute(text(statement))


def test_duplicate_version_ordinal_fails(
    registered_document: DocumentSummary,
    auth_database: Engine,
) -> None:
    assert registered_document.active_version_id is not None
    with auth_database.begin() as connection:
        target = uuid4()
        copy_stored_version(connection, registered_document.active_version_id, target)
        copy_chunk_set(connection, registered_document.active_version_id, target)
        with pytest.raises(IntegrityError, match="version_ordinal"), connection.begin_nested():
            copy_chunk_set(connection, registered_document.active_version_id, target)


@pytest.mark.parametrize("change", ["foreign-document", "hash", "slice", "offset", "tokens"])
def test_chunk_must_match_canonical_version(
    registered_document: DocumentSummary,
    auth_database: Engine,
    change: str,
) -> None:
    assert registered_document.active_version_id is not None
    with auth_database.begin() as connection:
        target = uuid4()
        copy_stored_version(connection, registered_document.active_version_id, target)
        with pytest.raises(IntegrityError), connection.begin_nested():
            _ = connection.execute(
                text("""INSERT INTO document_chunks
            (id,document_id,document_version_id,ordinal,text,content_sha256,
             token_count,char_start,char_end)
            SELECT gen_random_uuid(),
            CASE WHEN :change='foreign-document' THEN gen_random_uuid() ELSE document_id END,
            :target,ordinal,CASE WHEN :change='slice' THEN 'bad' ELSE text END,
            CASE WHEN :change='hash' THEN repeat('a',64) ELSE content_sha256 END,
            CASE WHEN :change='tokens' THEN 401 ELSE token_count END,
            char_start,CASE WHEN :change='offset' THEN char_end+1 ELSE char_end END
            FROM document_chunks WHERE document_version_id=:source"""),
                {
                    "change": change,
                    "target": target,
                    "source": registered_document.active_version_id,
                },
            )


def test_sealed_chunk_set_rejects_late_insert(
    registered_document: DocumentSummary,
    auth_database: Engine,
) -> None:
    assert registered_document.active_version_id is not None
    with auth_database.begin() as connection, pytest.raises(IntegrityError, match="canonical"):
        copy_chunk_set(
            connection, registered_document.active_version_id, registered_document.active_version_id
        )


def test_empty_chunk_set_cannot_be_marked_chunked(
    registered_document: DocumentSummary,
    auth_database: Engine,
) -> None:
    assert registered_document.active_version_id is not None
    with auth_database.begin() as connection:
        target = uuid4()
        copy_stored_version(connection, registered_document.active_version_id, target)
        with pytest.raises(IntegrityError, match="Incomplete"), connection.begin_nested():
            _ = connection.execute(
                text("UPDATE document_versions SET status='chunked' WHERE id=:id"), {"id": target}
            )


@pytest.mark.parametrize("corruption", ["hash", "slice", "empty"])
def test_chunk_failure_preserves_previous_active_version(
    registered_document: DocumentSummary,
    auth_database: Engine,
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    original = chunk_text

    def corrupted(text: str, tokenizer: E5TokenCounter) -> tuple[ChunkDraft, ...]:
        chunks = original(text, tokenizer)
        if corruption == "empty":
            return ()
        replacement = (
            replace(chunks[0], content_sha256="0" * 64)
            if corruption == "hash"
            else (replace(chunks[0], text="substituted"))
        )
        return (replacement, *chunks[1:])

    monkeypatch.setattr(ingestion, "chunk_text", corrupted)
    with auth_database.connect() as connection:
        revision = connection.execute(select(PolicyState.revision)).scalar_one()
    response = admin_client.post(
        f"/api/admin/documents/{registered_document.id}/versions",
        files={"file": ("new.txt", b"new text", "text/plain")},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 422
    with auth_database.connect() as connection:
        assert (
            connection.execute(text("SELECT revision FROM policy_state")).scalar_one() == revision
        )
        assert connection.execute(text("SELECT count(*) FROM document_versions")).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM document_chunks")).scalar_one() == 1
        assert (
            connection.execute(text("SELECT active_version_id FROM documents")).scalar_one()
            == registered_document.active_version_id
        )
