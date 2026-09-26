"""Reauthorize complete source identities inside the protected read transaction."""

from sqlalchemy import select

from rag_access_guard import Guard, SourceRef
from rag_access_guard_api.adapters.llm import FakeTokenCounter
from rag_access_guard_api.adapters.policy import PostgresPolicyReader
from rag_access_guard_api.persistence import Document, DocumentChunk
from rag_access_guard_api.schemas.sources import SourceContent, SourceNotFound
from rag_access_guard_api.services.security import ReadUoW


def build_source_url(ref: SourceRef) -> str:
    """Build a relative application URL exclusively from a validated source tuple."""
    return (
        f"/api/documents/{ref.document_id}/versions/{ref.document_version_id}/content"
        f"?chunk_id={ref.chunk_id}"
    )


async def read_source(uow: ReadUoW, source_ref: SourceRef) -> SourceContent:
    """Load a chunk only after the shared provenance and access gate allows it."""
    decision = await Guard(FakeTokenCounter()).authorize_read(
        uow.principal.principal_id, (source_ref,), PostgresPolicyReader(uow)
    )
    if not decision.allowed:
        raise SourceNotFound
    row = (
        (
            await uow.connection.execute(
                select(Document.title, DocumentChunk.text)
                .join(DocumentChunk, DocumentChunk.document_id == Document.id)
                .where(
                    Document.id == source_ref.document_id,
                    DocumentChunk.document_version_id == source_ref.document_version_id,
                    DocumentChunk.id == source_ref.chunk_id,
                )
            )
        )
        .tuples()
        .one_or_none()
    )
    if row is None:
        raise SourceNotFound
    return SourceContent(
        document_id=source_ref.document_id,
        document_version_id=source_ref.document_version_id,
        chunk_id=source_ref.chunk_id,
        title=row[0],
        text=row[1],
    )
