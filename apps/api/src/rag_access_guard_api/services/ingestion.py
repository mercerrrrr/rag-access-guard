"""Prepare immutable content before acquiring the security mutation lock."""

import json
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID, uuid4

from anyio import to_thread
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from rag_access_guard_api.adapters.text_parser import parse_text
from rag_access_guard_api.adapters.tokenizer import get_tokenizer
from rag_access_guard_api.persistence import Document, DocumentChunk, DocumentVersion
from rag_access_guard_api.schemas.documents import DocumentVersionSummary
from rag_access_guard_api.schemas.ingestion import IngestionManifest, ParsedDocument, UploadPayload
from rag_access_guard_api.services.audit import AuditRecord
from rag_access_guard_api.services.chunking import CHUNKER_REVISION, ChunkDraft, chunk_text
from rag_access_guard_api.services.errors import ForbiddenError
from rag_access_guard_api.services.security import MutationUoW, PolicyUnitOfWork
from rag_access_guard_api.services.text_documents import DocumentError
from rag_access_guard_api.services.tokens import matches_token


@dataclass(frozen=True, slots=True)
class PreparedUpload:
    """One validated upload and the manifest computed from its actual bytes."""

    upload: UploadPayload
    parsed: ParsedDocument
    manifest: IngestionManifest
    chunks: tuple[ChunkDraft, ...]


def prepare_upload(upload: UploadPayload) -> PreparedUpload:
    """Run on a worker thread, outside all database transactions."""
    parsed = parse_text(upload)
    tokenizer = get_tokenizer()
    chunks = chunk_text(parsed.text, tokenizer)
    config = json.dumps(
        {
            "parser_revision": parsed.parser_revision,
            "chunker_revision": CHUNKER_REVISION,
            "tokenizer_revision": tokenizer.identity,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    manifest = IngestionManifest(
        schema_version=1,
        source_sha256=sha256(upload.data).hexdigest(),
        text_sha256=sha256(parsed.text.encode("utf-8")).hexdigest(),
        byte_size=len(upload.data),
        parser_revision=parsed.parser_revision,
        chunker_revision=CHUNKER_REVISION,
        tokenizer_revision=tokenizer.identity,
        embedding_model_id=None,
        embedding_model_revision=None,
        config_sha256=sha256(config.encode("utf-8")).hexdigest(),
    )
    return PreparedUpload(upload, parsed, manifest, chunks)


async def store_version(
    uow: MutationUoW, document_id: UUID, prepared: PreparedUpload
) -> DocumentVersionSummary:
    """Insert and activate within an already authorized document mutation."""
    manifest = prepared.manifest
    row = (
        (
            await uow.connection.execute(
                insert(DocumentVersion)
                .values(
                    id=uuid4(),
                    document_id=document_id,
                    original_bytes=prepared.upload.data,
                    content_sha256=manifest.source_sha256,
                    extracted_text=prepared.parsed.text,
                    text_sha256=manifest.text_sha256,
                    media_type=prepared.parsed.media_type,
                    byte_size=manifest.byte_size,
                    parser_revision=manifest.parser_revision,
                    status="stored",
                    failure_code=None,
                    ingestion_manifest=manifest.model_dump(),
                    created_by=uow.principal.principal_id,
                )
                .returning(
                    DocumentVersion.id,
                    DocumentVersion.document_id,
                    DocumentVersion.status,
                    DocumentVersion.created_at,
                    DocumentVersion.content_sha256,
                    DocumentVersion.byte_size,
                )
            )
        )
        .mappings()
        .one()
    )
    version = DocumentVersionSummary.model_validate(row)
    if not prepared.chunks:
        raise DocumentError(422, "parse_failed")
    for chunk in prepared.chunks:
        if (
            not 0 <= chunk.char_start < chunk.char_end <= len(prepared.parsed.text)
            or chunk.text != prepared.parsed.text[chunk.char_start : chunk.char_end]
            or sha256(chunk.text.encode("utf-8")).hexdigest() != chunk.content_sha256
        ):
            raise DocumentError(422, "parse_failed")
    _ = await uow.connection.execute(
        insert(DocumentChunk),
        [
            {
                "id": uuid4(),
                "document_id": document_id,
                "document_version_id": version.id,
                "ordinal": chunk.ordinal,
                "text": chunk.text,
                "content_sha256": chunk.content_sha256,
                "token_count": chunk.token_count,
                "char_start": chunk.char_start,
                "char_end": chunk.char_end,
            }
            for chunk in prepared.chunks
        ],
    )
    _ = await uow.connection.execute(
        update(DocumentVersion).where(DocumentVersion.id == version.id).values(status="chunked")
    )
    _ = await uow.connection.execute(
        update(Document).where(Document.id == document_id).values(active_version_id=version.id)
    )
    _ = await uow.record_change(
        AuditRecord(
            event_type="document_changed",
            stage="policy",
            outcome="success",
            actor_user_id=uow.principal.principal_id,
            principal_id=uow.principal.principal_id,
            document_id=document_id,
        )
    )
    return version.model_copy(update={"status": "chunked"})


async def ingest_text_version(
    engine: AsyncEngine,
    session_token: str,
    document_id: UUID,
    *,
    upload: UploadPayload,
    csrf_token: str,
) -> DocumentVersionSummary:
    """Recheck live authority after parsing, then atomically replace the active version."""
    prepared = await to_thread.run_sync(prepare_upload, upload)
    async with PolicyUnitOfWork(engine).mutation(session_token) as uow:
        if not uow.principal.is_admin or not matches_token(csrf_token, uow.csrf_digest):
            raise ForbiddenError
        if (
            await uow.connection.execute(
                select(Document.id).where(Document.id == document_id).with_for_update()
            )
        ).scalar_one_or_none() is None:
            raise DocumentError(404)
        return await store_version(uow, document_id, prepared)
