"""Exact cosine retrieval over a materialized authorized candidate set."""

from pgvector.sqlalchemy import Vector
from sqlalchemy import Integer, String, Uuid, bindparam, column, func, select

from rag_access_guard import CandidateChunk
from rag_access_guard_api.adapters.canonical import CanonicalChunk, canonical_query
from rag_access_guard_api.adapters.embeddings import MODEL_ID
from rag_access_guard_api.adapters.tokenizer import MODEL_REVISION, TOKENIZER_IDENTITY
from rag_access_guard_api.adapters.vector_scoring import cosine_distance
from rag_access_guard_api.config import Settings
from rag_access_guard_api.persistence import (
    ChunkEmbedding,
    Document,
    DocumentChunk,
    DocumentVersion,
)
from rag_access_guard_api.schemas.embedding_vectors import EmbeddingError, validate_vectors
from rag_access_guard_api.schemas.retrieval_config import load_retrieval_config
from rag_access_guard_api.schemas.search import MAX_RESULTS, InvalidSearchError
from rag_access_guard_api.services.access import readable_document
from rag_access_guard_api.services.security import ReadUoW


async def retrieve(
    uow: ReadUoW, query_embedding: tuple[float, ...], *, limit: int = 5
) -> tuple[CandidateChunk, ...]:
    """Apply policy before threshold and top-k; copy only canonical allowed text."""
    if not 1 <= limit <= MAX_RESULTS or not uow.connection.in_transaction():
        raise InvalidSearchError
    try:
        validate_vectors((query_embedding,), 1)
    except EmbeddingError as error:
        raise InvalidSearchError from error
    config = load_retrieval_config(Settings().retrieval_config_path)
    eligible = (
        canonical_query()
        .add_columns(ChunkEmbedding.embedding, DocumentChunk.ordinal)
        .join(Document, Document.id == DocumentChunk.document_id)
        .join(ChunkEmbedding, ChunkEmbedding.chunk_id == DocumentChunk.id)
        .where(
            readable_document(uow.principal.principal_id),
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
    distance = cosine_distance(
        column("embedding", Vector(384)), bindparam("query", type_=Vector(384))
    )
    query = (
        select(eligible)
        .where(1.0 - distance >= config.threshold)
        .order_by(
            distance,
            column("document_id", Uuid()),
            column("document_version_id", Uuid()),
            column("ordinal", Integer()),
            column("chunk_id", Uuid()),
        )
        .limit(limit)
    )
    rows = (await uow.connection.execute(query, {"query": list(query_embedding)})).mappings()
    return tuple(CanonicalChunk.model_validate(row).candidate() for row in rows)
