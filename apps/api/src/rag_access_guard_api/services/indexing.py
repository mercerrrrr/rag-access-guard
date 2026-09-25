"""Compute outside policy locks and compare the active pointer before publication."""

import json
import re
from dataclasses import replace
from hashlib import sha256
from uuid import UUID

from anyio import to_thread
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from rag_access_guard_api.adapters import embeddings
from rag_access_guard_api.adapters.embeddings import EmbeddingAdapter
from rag_access_guard_api.adapters.pdf_protocol import parser_options
from rag_access_guard_api.persistence import Document, DocumentVersion
from rag_access_guard_api.schemas.documents import DocumentVersionSummary
from rag_access_guard_api.schemas.embedding_vectors import (
    EMBEDDING_DIMENSION,
    EmbeddingError,
    VersionActivationConflictError,
    validate_vectors,
)
from rag_access_guard_api.schemas.ingestion import UploadPayload
from rag_access_guard_api.services import ingestion
from rag_access_guard_api.services.errors import ForbiddenError
from rag_access_guard_api.services.security import MutationUoW, PolicyUnitOfWork, ReadUoW
from rag_access_guard_api.services.text_documents import DocumentError
from rag_access_guard_api.services.tokens import matches_token


def require_index_authority(uow: ReadUoW | MutationUoW, csrf_token: str) -> None:
    """Administrative computation never bypasses the bound session CSRF token."""
    if not uow.principal.is_admin or not matches_token(csrf_token, uow.csrf_digest):
        raise ForbiddenError


async def prepare_index(
    upload: UploadPayload, embedder: EmbeddingAdapter
) -> ingestion.PreparedUpload:
    """Bind the full recipe and reject an invalid batch as one failed attempt."""
    prepared = await to_thread.run_sync(ingestion.prepare_upload, upload)
    if embedder.model_id != embeddings.MODEL_ID or not re.fullmatch(
        "[0-9a-f]{40}", embedder.revision
    ):
        raise EmbeddingError
    config = json.dumps(
        {
            "parser_revision": prepared.manifest.parser_revision,
            **(
                {"parser_options": parser_options()}
                if prepared.parsed.media_type == "application/pdf"
                else {}
            ),
            "chunker_revision": prepared.manifest.chunker_revision,
            "tokenizer_revision": prepared.manifest.tokenizer_revision,
            "embedding_model_id": embedder.model_id,
            "embedding_model_revision": embedder.revision,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    manifest = prepared.manifest.model_copy(
        update={
            "embedding_model_id": embedder.model_id,
            "embedding_model_revision": embedder.revision,
            "config_sha256": sha256(config.encode()).hexdigest(),
        }
    )
    if embedder.dimension != EMBEDDING_DIMENSION:
        return replace(prepared, manifest=manifest, vectors=())
    try:
        vectors = await embedder.embed_passages(tuple(chunk.text for chunk in prepared.chunks))
        validate_vectors(vectors, len(prepared.chunks))
    except EmbeddingError:
        vectors = ()
    return replace(prepared, manifest=manifest, vectors=vectors)


async def ingest_text_version(  # noqa: PLR0913
    engine: AsyncEngine,
    session_token: str,
    document_id: UUID,
    *,
    upload: UploadPayload,
    csrf_token: str,
    embedder: EmbeddingAdapter | None = None,
) -> DocumentVersionSummary:
    """A newer activation wins even if an older inference finishes afterward."""
    policy = PolicyUnitOfWork(engine)
    async with policy.protected_read(session_token) as uow:
        require_index_authority(uow, csrf_token)
        original = (
            (
                await uow.connection.execute(
                    select(Document.active_version_id, Document.is_active).where(
                        Document.id == document_id
                    )
                )
            )
            .tuples()
            .one_or_none()
        )
        if original is None:
            raise DocumentError(404)
        if not original[1]:
            raise ForbiddenError
        expected = original[0]
    prepared = await prepare_index(upload, embedder or embeddings.get_embedding_adapter())
    return await _publish_index(
        policy,
        session_token,
        document_id,
        expected=expected,
        prepared=prepared,
        csrf_token=csrf_token,
    )


async def _publish_index(  # noqa: PLR0913
    policy: PolicyUnitOfWork,
    session_token: str,
    document_id: UUID,
    *,
    expected: UUID | None,
    prepared: ingestion.PreparedUpload,
    csrf_token: str,
) -> DocumentVersionSummary:
    async with policy.mutation(session_token) as mutation:
        require_index_authority(mutation, csrf_token)
        current = (
            (
                await mutation.connection.execute(
                    select(Document.active_version_id, Document.is_active)
                    .where(Document.id == document_id)
                    .with_for_update()
                )
            )
            .tuples()
            .one()
        )
        if not current[1]:
            raise ForbiddenError
        version = await ingestion.store_version(mutation, document_id, prepared)
        conflict = current[0] != expected
        if version.status == "ready" and not conflict:
            await ingestion.activate_version(mutation, version)
    if version.status == "failed":
        raise EmbeddingError
    if conflict:
        raise VersionActivationConflictError
    return version


async def index_document_version(  # noqa: PLR0913
    engine: AsyncEngine,
    session_token: str,
    document_id: UUID,
    version_id: UUID,
    *,
    embedder: EmbeddingAdapter,
    csrf_token: str,
) -> DocumentVersionSummary:
    """Reprocess server-held bytes into a fresh immutable version and model recipe."""
    policy = PolicyUnitOfWork(engine)
    async with policy.protected_read(session_token) as uow:
        require_index_authority(uow, csrf_token)
        row = (
            (
                await uow.connection.execute(
                    select(
                        DocumentVersion.original_bytes,
                        DocumentVersion.media_type,
                        Document.active_version_id,
                        Document.is_active,
                    )
                    .join(Document, Document.id == DocumentVersion.document_id)
                    .where(
                        DocumentVersion.document_id == document_id, DocumentVersion.id == version_id
                    )
                )
            )
            .tuples()
            .one_or_none()
        )
        if row is None:
            raise DocumentError(404)
        if not row[3]:
            raise ForbiddenError
        filename = "source.pdf" if row[1] == "application/pdf" else "source.md"
        upload = UploadPayload(filename=filename, media_type=row[1], data=row[0])
        expected = row[2]
    prepared = await prepare_index(upload, embedder)
    return await _publish_index(
        policy,
        session_token,
        document_id,
        expected=expected,
        prepared=prepared,
        csrf_token=csrf_token,
    )
