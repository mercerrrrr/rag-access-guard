"""Prepare immutable content before acquiring the security mutation lock."""

import json
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import insert, update

from rag_access_guard_api.adapters.embeddings import MODEL_ID
from rag_access_guard_api.adapters.pdf_parser import parse_pdf
from rag_access_guard_api.adapters.pdf_protocol import parser_options
from rag_access_guard_api.adapters.text_parser import parse_text
from rag_access_guard_api.adapters.tokenizer import MODEL_REVISION, get_tokenizer
from rag_access_guard_api.persistence import (
    ChunkEmbedding,
    Document,
    DocumentChunk,
    DocumentVersion,
)
from rag_access_guard_api.schemas.documents import DocumentVersionSummary
from rag_access_guard_api.schemas.embedding_vectors import EmbeddingError, validate_vectors
from rag_access_guard_api.schemas.ingestion import IngestionManifest, ParsedDocument, UploadPayload
from rag_access_guard_api.services.audit import AuditRecord
from rag_access_guard_api.services.chunking import CHUNKER_REVISION, ChunkDraft, chunk_text
from rag_access_guard_api.services.security import MutationUoW
from rag_access_guard_api.services.text_documents import DocumentError


@dataclass(frozen=True, slots=True)
class PreparedUpload:
    """One validated upload and the manifest computed from its actual bytes."""

    upload: UploadPayload
    parsed: ParsedDocument
    manifest: IngestionManifest
    chunks: tuple[ChunkDraft, ...]
    vectors: tuple[tuple[float, ...], ...] | None = None


def prepare_upload(upload: UploadPayload) -> PreparedUpload:
    """Run on a worker thread, outside all database transactions."""
    parsed = parse_pdf(upload) if upload.filename.lower().endswith(".pdf") else parse_text(upload)
    tokenizer = get_tokenizer()
    chunks = chunk_text(parsed.text, tokenizer)
    config = json.dumps(
        {
            "parser_revision": parsed.parser_revision,
            **(
                {"parser_options": parser_options()}
                if parsed.media_type == "application/pdf"
                else {}
            ),
            "chunker_revision": CHUNKER_REVISION,
            "tokenizer_revision": tokenizer.identity,
            "embedding_model_id": MODEL_ID,
            "embedding_model_revision": MODEL_REVISION,
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
        embedding_model_id=MODEL_ID,
        embedding_model_revision=MODEL_REVISION,
        config_sha256=sha256(config.encode("utf-8")).hexdigest(),
    )
    return PreparedUpload(upload, parsed, manifest, chunks)


async def store_version(
    uow: MutationUoW, document_id: UUID, prepared: PreparedUpload
) -> DocumentVersionSummary:
    """Store a complete ready or failed artifact without changing the active pointer."""
    if prepared.vectors is None:
        raise EmbeddingError
    if prepared.vectors:
        validate_vectors(prepared.vectors, len(prepared.chunks))
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
    chunk_ids = tuple(uuid4() for _ in prepared.chunks)
    _ = await uow.connection.execute(
        insert(DocumentChunk),
        [
            {
                "id": chunk_id,
                "document_id": document_id,
                "document_version_id": version.id,
                "ordinal": chunk.ordinal,
                "text": chunk.text,
                "content_sha256": chunk.content_sha256,
                "token_count": chunk.token_count,
                "char_start": chunk.char_start,
                "char_end": chunk.char_end,
            }
            for chunk_id, chunk in zip(chunk_ids, prepared.chunks, strict=True)
        ],
    )
    _ = await uow.connection.execute(
        update(DocumentVersion).where(DocumentVersion.id == version.id).values(status="chunked")
    )
    _ = await uow.connection.execute(
        update(DocumentVersion).where(DocumentVersion.id == version.id).values(status="indexing")
    )
    if not prepared.vectors:
        _ = await uow.connection.execute(
            update(DocumentVersion)
            .where(DocumentVersion.id == version.id)
            .values(status="failed", failure_code="index_failed")
        )
        return version.model_copy(update={"status": "failed"})
    _ = await uow.connection.execute(
        insert(ChunkEmbedding),
        [
            {
                "chunk_id": chunk_id,
                "embedding": list(vector),
                "model_id": manifest.embedding_model_id,
                "model_revision": manifest.embedding_model_revision,
            }
            for chunk_id, vector in zip(chunk_ids, prepared.vectors, strict=True)
        ],
    )
    _ = await uow.connection.execute(
        update(DocumentVersion).where(DocumentVersion.id == version.id).values(status="ready")
    )
    return version.model_copy(update={"status": "ready"})


async def activate_version(uow: MutationUoW, version: DocumentVersionSummary) -> None:
    """Publish a ready artifact together with its security revision and audit."""
    if version.status != "ready":
        raise EmbeddingError
    _ = await uow.connection.execute(
        update(Document)
        .where(Document.id == version.document_id)
        .values(active_version_id=version.id)
    )
    _ = await uow.record_change(
        AuditRecord(
            event_type="document_changed",
            stage="policy",
            outcome="success",
            actor_user_id=uow.principal.principal_id,
            principal_id=uow.principal.principal_id,
            document_id=version.document_id,
        )
    )
