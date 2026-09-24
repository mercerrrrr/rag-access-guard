"""Administrative reprocessing creates a new artifact, never edits provenance."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from rag_access_guard_api.persistence import DocumentVersion
from rag_access_guard_api.schemas.documents import DocumentVersionSummary
from rag_access_guard_api.schemas.ingestion import UploadPayload
from rag_access_guard_api.services.errors import ForbiddenError
from rag_access_guard_api.services.ingestion import ingest_text_version
from rag_access_guard_api.services.security import PolicyUnitOfWork
from rag_access_guard_api.services.text_documents import DocumentError
from rag_access_guard_api.services.tokens import matches_token


async def rechunk_version(
    engine: AsyncEngine,
    session_token: str,
    document_id: UUID,
    version_id: UUID,
    *,
    csrf_token: str,
) -> DocumentVersionSummary:
    """Read stored bytes under a fresh admin gate; save through the ingestion gate."""
    async with PolicyUnitOfWork(engine).protected_read(session_token) as uow:
        if not uow.principal.is_admin or not matches_token(csrf_token, uow.csrf_digest):
            raise ForbiddenError
        row = (
            (
                await uow.connection.execute(
                    select(DocumentVersion.original_bytes, DocumentVersion.media_type).where(
                        DocumentVersion.id == version_id,
                        DocumentVersion.document_id == document_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise DocumentError(404)
        source = UploadPayload.model_validate(
            {
                "filename": "source.md",
                "media_type": row["media_type"],
                "data": row["original_bytes"],
            }
        )
    return await ingest_text_version(
        engine,
        session_token,
        document_id,
        upload=source,
        csrf_token=csrf_token,
    )
