"""One document predicate for session-scoped listing and canonical text reads."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.sql.elements import ColumnElement

from rag_access_guard import AccessDecision
from rag_access_guard_api.persistence import Document, DocumentGrant, DocumentVersion, UserRole
from rag_access_guard_api.schemas.access import AccessibleDocument, DocumentText
from rag_access_guard_api.services.security import ReadUoW
from rag_access_guard_api.services.text_documents import DocumentError


@dataclass(frozen=True, slots=True)
class DocumentVersionRef:
    """Registry identity before chunk-level provenance exists."""

    document_id: UUID
    document_version_id: UUID


def readable_document(principal_id: UUID) -> ColumnElement[bool]:
    """Allow an active stored version through any independent explicit grant."""
    roles = select(UserRole.role_id).where(UserRole.user_id == principal_id)
    granted = (
        select(DocumentGrant.id)
        .where(
            DocumentGrant.document_id == Document.id,
            or_(DocumentGrant.user_id == principal_id, DocumentGrant.role_id.in_(roles)),
        )
        .exists()
    )
    stored = (
        select(DocumentVersion.id)
        .where(
            DocumentVersion.document_id == Document.id,
            DocumentVersion.id == Document.active_version_id,
            DocumentVersion.status.in_(("stored", "chunked", "ready")),
        )
        .correlate(Document)
        .exists()
    )
    return and_(Document.is_active.is_(True), stored, granted)


async def can_read_version(uow: ReadUoW, ref: DocumentVersionRef) -> AccessDecision:
    """Evaluate current access after the UoW has gated the active session."""
    allowed = (
        await uow.connection.execute(
            select(
                select(Document.id)
                .where(
                    Document.id == ref.document_id,
                    Document.active_version_id == ref.document_version_id,
                    readable_document(uow.principal.principal_id),
                )
                .exists()
            )
        )
    ).scalar_one()
    return AccessDecision(
        allowed=allowed, reason="allowed" if allowed else "denied", policy_revision=uow.revision
    )


async def list_accessible_documents(uow: ReadUoW) -> tuple[AccessibleDocument, ...]:
    """Use the same policy predicate without fetching denied titles or text."""
    rows = (
        await uow.connection.execute(
            select(Document.id, Document.title, Document.active_version_id)
            .where(readable_document(uow.principal.principal_id))
            .order_by(Document.title, Document.id)
        )
    ).mappings()
    return tuple(AccessibleDocument.model_validate(row) for row in rows)


async def read_document_text(uow: ReadUoW, ref: DocumentVersionRef) -> DocumentText:
    """Read canonical text only after authorization in the same transaction."""
    if not (await can_read_version(uow, ref)).allowed:
        raise DocumentError(404)
    canonical = (
        await uow.connection.execute(
            select(DocumentVersion.extracted_text).where(
                DocumentVersion.document_id == ref.document_id,
                DocumentVersion.id == ref.document_version_id,
            )
        )
    ).scalar_one()
    return DocumentText(
        document_id=ref.document_id, document_version_id=ref.document_version_id, text=canonical
    )
