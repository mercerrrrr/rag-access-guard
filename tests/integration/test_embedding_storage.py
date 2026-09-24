from uuid import uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DataError, IntegrityError

from rag_access_guard_api.schemas.documents import DocumentSummary
from tests.integration.chunk_fixtures import copy_chunk_set, copy_stored_version


def test_ready_version_has_one_vector_per_chunk(
    registered_document: DocumentSummary,
    auth_database: Engine,
) -> None:
    assert registered_document.active_version_id is not None
    with auth_database.connect() as connection:
        assert (
            connection.execute(text("SELECT status FROM document_versions")).scalar_one() == "ready"
        )
        assert connection.execute(text("SELECT count(*) FROM chunk_embeddings")).scalar_one() == 1
        assert (
            connection.execute(
                text("SELECT vector_dims(embedding) FROM chunk_embeddings")
            ).scalar_one()
            == 384
        )
        assert connection.execute(
            text("SELECT abs(vector_norm(embedding)-1)<0.00001 FROM chunk_embeddings")
        ).scalar_one()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE chunk_embeddings SET embedding=embedding",
        "UPDATE chunk_embeddings SET model_revision=model_revision",
        "DELETE FROM chunk_embeddings",
    ],
)
def test_embedding_mutation_is_rejected(
    registered_document: DocumentSummary,
    auth_database: Engine,
    statement: str,
) -> None:
    assert registered_document.active_version_id is not None
    with auth_database.begin() as connection, pytest.raises(IntegrityError, match="immutable"):
        _ = connection.execute(text(statement))


def test_partial_embedding_set_cannot_be_marked_ready(
    registered_document: DocumentSummary,
    auth_database: Engine,
) -> None:
    assert registered_document.active_version_id is not None
    with auth_database.begin() as connection:
        target = uuid4()
        copy_stored_version(connection, registered_document.active_version_id, target)
        copy_chunk_set(connection, registered_document.active_version_id, target)
        for state in ("chunked", "indexing"):
            _ = connection.execute(
                text("UPDATE document_versions SET status=:state WHERE id=:id"),
                {"id": target, "state": state},
            )
        with pytest.raises(IntegrityError, match="Incomplete embedding"), connection.begin_nested():
            _ = connection.execute(
                text("UPDATE document_versions SET status='ready' WHERE id=:id"), {"id": target}
            )


@pytest.mark.parametrize("corruption", ["foreign-chunk", "revision", "model", "dimension", "norm"])
def test_embedding_must_match_chunk_and_model_manifest(
    registered_document: DocumentSummary,
    auth_database: Engine,
    corruption: str,
) -> None:
    assert registered_document.active_version_id is not None
    with auth_database.begin() as connection:
        target = uuid4()
        copy_stored_version(connection, registered_document.active_version_id, target)
        copy_chunk_set(connection, registered_document.active_version_id, target)
        for state in ("chunked", "indexing"):
            _ = connection.execute(
                text("UPDATE document_versions SET status=:state WHERE id=:id"),
                {"id": target, "state": state},
            )
        expected_error = DataError if corruption == "dimension" else IntegrityError
        with pytest.raises(expected_error), connection.begin_nested():
            _ = connection.execute(
                text("""INSERT INTO chunk_embeddings(chunk_id,embedding,model_id,model_revision)
                SELECT CASE WHEN :bad='foreign-chunk' THEN gen_random_uuid() ELSE c.id END,
                CASE WHEN :bad='dimension' THEN '[1,0]'::vector
                     WHEN :bad='norm' THEN array_fill(0::real,ARRAY[384])::vector
                     ELSE e.embedding END,
                CASE WHEN :bad='model' THEN 'wrong' ELSE e.model_id END,
                CASE WHEN :bad='revision' THEN repeat('a',40) ELSE e.model_revision END
                FROM document_chunks c CROSS JOIN chunk_embeddings e
                WHERE c.document_version_id=:id"""),
                {"bad": corruption, "id": target},
            )
