"""Reauthorize complete source identities inside the protected read transaction."""

import logging
from hashlib import sha256

from pydantic import ValidationError
from sqlalchemy import select

from rag_access_guard import Guard, SourceRef
from rag_access_guard_api.adapters.docx_protocol import DOCX_MIME
from rag_access_guard_api.adapters.llm import FakeTokenCounter
from rag_access_guard_api.adapters.policy import PostgresPolicyReader
from rag_access_guard_api.persistence import Document, DocumentChunk, DocumentVersion
from rag_access_guard_api.schemas.ingestion import IngestionManifest
from rag_access_guard_api.schemas.sources import OriginalContent, SourceContent, SourceNotFound
from rag_access_guard_api.services.origin import read_version_origin
from rag_access_guard_api.services.security import ReadUoW
from rag_access_guard_api.services.text_documents import MAX_TEXT_BYTES


def build_source_url(ref: SourceRef) -> str:
    """Build a relative application URL exclusively from a validated source tuple."""
    return (
        f"/api/documents/{ref.document_id}/versions/{ref.document_version_id}/content"
        f"?chunk_id={ref.chunk_id}"
    )


async def _authorize_source(uow: ReadUoW, source_ref: SourceRef) -> None:
    decision = await Guard(FakeTokenCounter()).authorize_read(
        uow.principal.principal_id, (source_ref,), PostgresPolicyReader(uow)
    )
    if not decision.allowed:
        raise SourceNotFound


async def read_source(uow: ReadUoW, source_ref: SourceRef) -> SourceContent:
    """Load a chunk only after the shared provenance and access gate allows it."""
    await _authorize_source(uow, source_ref)
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
        origin=await read_version_origin(uow.connection, source_ref.document_version_id),
    )


async def read_original(uow: ReadUoW, source_ref: SourceRef) -> OriginalContent:
    """Copy the active version's exact bytes before releasing the policy lock."""
    await _authorize_source(uow, source_ref)
    row = (
        (
            await uow.connection.execute(
                select(
                    DocumentVersion.original_bytes,
                    DocumentVersion.content_sha256,
                    DocumentVersion.byte_size,
                    DocumentVersion.media_type,
                    DocumentVersion.ingestion_manifest,
                    DocumentVersion.parser_revision,
                    DocumentVersion.text_sha256,
                    DocumentVersion.extracted_text,
                ).where(
                    DocumentVersion.document_id == source_ref.document_id,
                    DocumentVersion.id == source_ref.document_version_id,
                )
            )
        )
        .tuples()
        .one_or_none()
    )
    if row is None:
        raise SourceNotFound
    data, digest, size, media_type, raw_manifest, parser, text_digest, extracted = row
    try:
        manifest = IngestionManifest.model_validate(raw_manifest)
    except ValidationError:
        raise SourceNotFound from None
    extension = {
        "text/plain": "txt",
        "text/markdown": "md",
        "application/pdf": "pdf",
        DOCX_MIME: "docx",
    }.get(media_type)
    if (
        extension is None
        or not 0 < len(data) <= MAX_TEXT_BYTES
        or len(data) != size
        or size != manifest.byte_size
        or sha256(data).hexdigest() != digest
        or digest != manifest.source_sha256
        or sha256(extracted.encode("utf-8")).hexdigest() != text_digest
        or text_digest != manifest.text_sha256
        or parser != manifest.parser_revision
    ):
        logging.getLogger(__name__).warning("original_integrity_failed")
        raise SourceNotFound
    return OriginalContent(data, media_type, f"{source_ref.document_id}.{extension}")
