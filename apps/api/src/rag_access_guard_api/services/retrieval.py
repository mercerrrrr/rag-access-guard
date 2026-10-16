"""Exact cosine retrieval over a materialized authorized candidate set."""

from sqlalchemy import select

from rag_access_guard import CandidateChunk
from rag_access_guard_api.adapters.canonical import CanonicalChunk
from rag_access_guard_api.config import Settings
from rag_access_guard_api.schemas.embedding_vectors import EmbeddingError, validate_vectors
from rag_access_guard_api.schemas.retrieval_config import load_retrieval_config
from rag_access_guard_api.schemas.search import MAX_RESULTS, InvalidSearchError
from rag_access_guard_api.services.retrieval_query import (
    eligible_candidates,
    production_order,
    query_distance,
)
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
    eligible = eligible_candidates(uow.principal.principal_id)
    distance = query_distance()
    query = (
        select(eligible)
        .where(1.0 - distance >= config.threshold)
        .order_by(*production_order(distance))
        .limit(limit)
    )
    rows = (await uow.connection.execute(query, {"query": list(query_embedding)})).mappings()
    return tuple(CanonicalChunk.model_validate(row).candidate() for row in rows)
