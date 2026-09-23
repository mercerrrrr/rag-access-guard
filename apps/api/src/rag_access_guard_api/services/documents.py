"""Atomic logical document mutations and metadata-only queries."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.orm import InstrumentedAttribute

from rag_access_guard_api.persistence import Document, DocumentVersion
from rag_access_guard_api.schemas.documents import DocumentSummary, DocumentVersionSummary
from rag_access_guard_api.services.audit import AuditRecord
from rag_access_guard_api.services.errors import ForbiddenError
from rag_access_guard_api.services.ingestion import PreparedUpload, store_version
from rag_access_guard_api.services.security import MutationUoW, ReadUoW
from rag_access_guard_api.services.text_documents import DocumentError, validate_title


def require_admin(uow: ReadUoW | MutationUoW) -> None:
    """Administrative metadata authority never implies a content grant."""
    if not uow.principal.is_admin:
        raise ForbiddenError


def _summary_columns() -> tuple[
    InstrumentedAttribute[UUID],
    InstrumentedAttribute[str],
    InstrumentedAttribute[bool],
    InstrumentedAttribute[UUID | None],
    InstrumentedAttribute[datetime],
]:
    return (
        Document.id,
        Document.title,
        Document.is_active,
        Document.active_version_id,
        Document.created_at,
    )


async def _record_change(uow: MutationUoW, document_id: UUID) -> None:
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


async def register_text_document(
    uow: MutationUoW, title: str, prepared: PreparedUpload
) -> DocumentSummary:
    """Store a version, activate it and audit the change in one transaction."""
    require_admin(uow)
    title = validate_title(title)
    document_id = uuid4()
    _ = await uow.connection.execute(
        insert(Document).values(id=document_id, title=title, created_by=uow.principal.principal_id)
    )
    _ = await store_version(uow, document_id, prepared)
    row = (
        (
            await uow.connection.execute(
                select(*_summary_columns()).where(Document.id == document_id)
            )
        )
        .mappings()
        .one()
    )
    return DocumentSummary.model_validate(row)


async def update_document(
    uow: MutationUoW, document_id: UUID, *, title: str | None = None, is_active: bool | None = None
) -> DocumentSummary:
    """Change logical metadata; unchanged values do not advance policy revision."""
    require_admin(uow)
    if title is None and is_active is None:
        raise DocumentError(422)
    normalized = validate_title(title) if title is not None else None
    row = (
        (
            await uow.connection.execute(
                select(*_summary_columns()).where(Document.id == document_id).with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise DocumentError(404)
    current = DocumentSummary.model_validate(row)
    new_title = current.title if normalized is None else normalized
    new_active = current.is_active if is_active is None else is_active
    if (new_title, new_active) == (current.title, current.is_active):
        return current
    changed = (
        (
            await uow.connection.execute(
                update(Document)
                .where(Document.id == document_id)
                .values(title=new_title, is_active=new_active)
                .returning(*_summary_columns())
            )
        )
        .mappings()
        .one()
    )
    await _record_change(uow, document_id)
    return DocumentSummary.model_validate(changed)


async def list_documents(uow: ReadUoW) -> list[DocumentSummary]:
    """Select only administrative metadata in a stable order."""
    require_admin(uow)
    rows = (
        await uow.connection.execute(
            select(*_summary_columns()).order_by(Document.created_at, Document.id)
        )
    ).mappings()
    return [DocumentSummary.model_validate(row) for row in rows]


async def list_versions(uow: ReadUoW, document_id: UUID) -> list[DocumentVersionSummary]:
    """List version metadata without selecting original bytes or extracted text."""
    require_admin(uow)
    if (
        await uow.connection.execute(select(Document.id).where(Document.id == document_id))
    ).scalar_one_or_none() is None:
        raise DocumentError(404)
    rows = (
        await uow.connection.execute(
            select(
                DocumentVersion.id,
                DocumentVersion.document_id,
                DocumentVersion.status,
                DocumentVersion.created_at,
                DocumentVersion.content_sha256,
                DocumentVersion.byte_size,
            )
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.created_at, DocumentVersion.id)
        )
    ).mappings()
    return [DocumentVersionSummary.model_validate(row) for row in rows]
