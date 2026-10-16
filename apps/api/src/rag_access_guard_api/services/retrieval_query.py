"""One authorized materialized candidate set and stable production ranking order."""

from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import Integer, String, Uuid, bindparam, column, func
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import CTE

from rag_access_guard_api.adapters.canonical import canonical_query
from rag_access_guard_api.adapters.embeddings import MODEL_ID
from rag_access_guard_api.adapters.tokenizer import MODEL_REVISION, TOKENIZER_IDENTITY
from rag_access_guard_api.adapters.vector_scoring import cosine_distance
from rag_access_guard_api.persistence import (
    ChunkEmbedding,
    Document,
    DocumentChunk,
    DocumentVersion,
)
from rag_access_guard_api.services.access import readable_document


def eligible_candidates(principal_id: UUID) -> CTE:
    """Apply the exact active-ready canonical recipe and content grant predicates once."""
    return (
        canonical_query()
        .add_columns(ChunkEmbedding.embedding, DocumentChunk.ordinal)
        .join(Document, Document.id == DocumentChunk.document_id)
        .join(ChunkEmbedding, ChunkEmbedding.chunk_id == DocumentChunk.id)
        .where(
            readable_document(principal_id),
            DocumentVersion.document_id == Document.id,
            DocumentVersion.id == Document.active_version_id,
            DocumentVersion.status == "ready",
            func.jsonb_extract_path_text(
                DocumentVersion.ingestion_manifest, "tokenizer_revision", type_=String()
            )
            == TOKENIZER_IDENTITY,
            func.jsonb_extract_path_text(
                DocumentVersion.ingestion_manifest, "embedding_model_revision", type_=String()
            )
            == MODEL_REVISION,
            ChunkEmbedding.model_id == MODEL_ID,
            ChunkEmbedding.model_revision == MODEL_REVISION,
        )
        .cte("eligible")
        .prefix_with("MATERIALIZED")
    )


def query_distance() -> ColumnElement[float]:
    """Production exact cosine scoring, shared with actor-aware calibration."""
    return cosine_distance(column("embedding", Vector(384)), bindparam("query", type_=Vector(384)))


def production_order(
    distance: ColumnElement[float],
) -> tuple[
    ColumnElement[float],
    ColumnElement[UUID],
    ColumnElement[UUID],
    ColumnElement[int],
    ColumnElement[UUID],
]:
    """Retain document/version/ordinal/chunk UUID ties at the top-five boundary."""
    return (
        distance,
        column("document_id", Uuid()),
        column("document_version_id", Uuid()),
        column("ordinal", Integer()),
        column("chunk_id", Uuid()),
    )
