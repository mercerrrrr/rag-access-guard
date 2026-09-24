"""Bound query computation with fresh authorization after model inference."""

from sqlalchemy.ext.asyncio import AsyncEngine

from rag_access_guard import CandidateChunk
from rag_access_guard_api.adapters.embeddings import MODEL_ID, EmbeddingAdapter
from rag_access_guard_api.adapters.tokenizer import (
    MODEL_REVISION,
    TokenizerUnavailableError,
    get_tokenizer,
)
from rag_access_guard_api.schemas.embedding_vectors import EmbeddingError, validate_vectors
from rag_access_guard_api.schemas.search import (
    MAX_QUERY_BYTES,
    MAX_RESULTS,
    InvalidSearchError,
    SearchError,
)
from rag_access_guard_api.services.chunking import EMBEDDING_LIMIT
from rag_access_guard_api.services.errors import ForbiddenError
from rag_access_guard_api.services.retrieval import retrieve
from rag_access_guard_api.services.security import PolicyUnitOfWork
from rag_access_guard_api.services.tokens import matches_token


async def search_documents(  # noqa: PLR0913
    engine: AsyncEngine,
    session_token: str,
    query: str,
    *,
    embedder: EmbeddingAdapter,
    csrf_token: str,
    limit: int = 5,
) -> tuple[CandidateChunk, ...]:
    """Authenticate before expensive work and recheck after the unlocked inference."""
    if (
        not query.strip()
        or len(query.encode("utf-8")) > MAX_QUERY_BYTES
        or not 1 <= limit <= MAX_RESULTS
    ):
        raise InvalidSearchError
    policy = PolicyUnitOfWork(engine)
    async with policy.protected_read(session_token) as initial:
        if not matches_token(csrf_token, initial.csrf_digest):
            raise ForbiddenError
    try:
        query_tokens = get_tokenizer().embedding_input_tokens(query, kind="query")
    except (OSError, TokenizerUnavailableError) as error:
        raise SearchError from error
    if query_tokens > EMBEDDING_LIMIT:
        raise InvalidSearchError
    if embedder.model_id != MODEL_ID or embedder.revision != MODEL_REVISION:
        raise EmbeddingError
    try:
        vector = await embedder.embed_query(query)
        validate_vectors((vector,), 1)
    except EmbeddingError as error:
        raise SearchError from error
    async with policy.protected_read(session_token) as uow:
        if not matches_token(csrf_token, uow.csrf_digest):
            raise ForbiddenError
        return await retrieve(uow, vector, limit=limit)
