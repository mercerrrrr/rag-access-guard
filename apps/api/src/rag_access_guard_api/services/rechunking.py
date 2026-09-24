"""Administrative reprocessing creates a new artifact, never edits provenance."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine

from rag_access_guard_api.adapters import embeddings
from rag_access_guard_api.schemas.documents import DocumentVersionSummary
from rag_access_guard_api.services.indexing import index_document_version


async def rechunk_version(
    engine: AsyncEngine,
    session_token: str,
    document_id: UUID,
    version_id: UUID,
    *,
    csrf_token: str,
) -> DocumentVersionSummary:
    """Rebuild chunks and embeddings under the same source-read activation snapshot."""
    return await index_document_version(
        engine,
        session_token,
        document_id,
        version_id,
        embedder=embeddings.get_embedding_adapter(),
        csrf_token=csrf_token,
    )
