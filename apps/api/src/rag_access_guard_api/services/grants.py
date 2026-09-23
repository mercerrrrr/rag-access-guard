"""Administrative direct grants and their atomic policy audit."""

from uuid import UUID, uuid4

from sqlalchemy import delete, insert, select

from rag_access_guard_api.persistence import Document, DocumentGrant, Role, User
from rag_access_guard_api.schemas.access import GrantView, UserSummary
from rag_access_guard_api.services.audit import AuditRecord
from rag_access_guard_api.services.documents import require_admin
from rag_access_guard_api.services.errors import GrantConflictError
from rag_access_guard_api.services.security import MutationUoW, ReadUoW
from rag_access_guard_api.services.text_documents import DocumentError


async def grant_user(uow: MutationUoW, document_id: UUID, user_id: UUID) -> GrantView:
    """Add one direct allow path under the exclusive policy lock."""
    require_admin(uow)
    if (
        await uow.connection.execute(select(Document.id).where(Document.id == document_id))
    ).scalar_one_or_none() is None:
        raise DocumentError(404)
    if (
        await uow.connection.execute(select(User.id).where(User.id == user_id))
    ).scalar_one_or_none() is None:
        raise DocumentError(404)
    if (
        await uow.connection.execute(
            select(DocumentGrant.id).where(
                DocumentGrant.document_id == document_id, DocumentGrant.user_id == user_id
            )
        )
    ).scalar_one_or_none() is not None:
        raise GrantConflictError
    row = (
        (
            await uow.connection.execute(
                insert(DocumentGrant)
                .values(
                    id=uuid4(),
                    document_id=document_id,
                    user_id=user_id,
                    created_by=uow.principal.principal_id,
                )
                .returning(
                    DocumentGrant.id,
                    DocumentGrant.document_id,
                    DocumentGrant.user_id,
                    DocumentGrant.role_id,
                )
            )
        )
        .mappings()
        .one()
    )
    grant = GrantView.model_validate(row)
    _ = await uow.record_change(
        AuditRecord(
            event_type="grant_added",
            stage="policy",
            outcome="success",
            actor_user_id=uow.principal.principal_id,
            principal_id=user_id,
            document_id=document_id,
            grant_id=grant.id,
        )
    )
    return grant


async def revoke_grant(uow: MutationUoW, document_id: UUID, grant_id: UUID) -> None:
    """Remove exactly the requested document's grant, preserving other paths."""
    require_admin(uow)
    row = (
        (
            await uow.connection.execute(
                delete(DocumentGrant)
                .where(DocumentGrant.id == grant_id, DocumentGrant.document_id == document_id)
                .returning(
                    DocumentGrant.id,
                    DocumentGrant.document_id,
                    DocumentGrant.user_id,
                    DocumentGrant.role_id,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise DocumentError(404)
    grant = GrantView.model_validate(row)
    _ = await uow.record_change(
        AuditRecord(
            event_type="grant_removed",
            stage="policy",
            outcome="success",
            actor_user_id=uow.principal.principal_id,
            principal_id=grant.user_id,
            role_id=grant.role_id,
            document_id=document_id,
            grant_id=grant.id,
        )
    )


async def grant_role(uow: MutationUoW, document_id: UUID, role_id: UUID) -> GrantView:
    """Add a role allow path with resource locks acquired in UUID order."""
    require_admin(uow)
    for identifier, model in sorted(
        ((document_id, Document), (role_id, Role)), key=lambda item: item[0]
    ):
        if (
            await uow.connection.execute(
                select(model.id).where(model.id == identifier).with_for_update()
            )
        ).scalar_one_or_none() is None:
            raise DocumentError(404)
    if (
        await uow.connection.execute(
            select(DocumentGrant.id).where(
                DocumentGrant.document_id == document_id, DocumentGrant.role_id == role_id
            )
        )
    ).scalar_one_or_none() is not None:
        raise GrantConflictError
    row = (
        (
            await uow.connection.execute(
                insert(DocumentGrant)
                .values(
                    id=uuid4(),
                    document_id=document_id,
                    role_id=role_id,
                    created_by=uow.principal.principal_id,
                )
                .returning(
                    DocumentGrant.id,
                    DocumentGrant.document_id,
                    DocumentGrant.user_id,
                    DocumentGrant.role_id,
                )
            )
        )
        .mappings()
        .one()
    )
    grant = GrantView.model_validate(row)
    _ = await uow.record_change(
        AuditRecord(
            event_type="grant_added",
            stage="policy",
            outcome="success",
            actor_user_id=uow.principal.principal_id,
            role_id=role_id,
            document_id=document_id,
            grant_id=grant.id,
        )
    )
    return grant


async def list_grants(uow: ReadUoW, document_id: UUID) -> tuple[GrantView, ...]:
    """Expose explicit grants only through the administrative read gate."""
    require_admin(uow)
    if (
        await uow.connection.execute(select(Document.id).where(Document.id == document_id))
    ).scalar_one_or_none() is None:
        raise DocumentError(404)
    rows = (
        await uow.connection.execute(
            select(
                DocumentGrant.id,
                DocumentGrant.document_id,
                DocumentGrant.user_id,
                DocumentGrant.role_id,
            )
            .where(DocumentGrant.document_id == document_id)
            .order_by(DocumentGrant.id)
        )
    ).mappings()
    return tuple(GrantView.model_validate(row) for row in rows)


async def list_users(uow: ReadUoW) -> tuple[UserSummary, ...]:
    """Return selection metadata without reading password hashes or sessions."""
    require_admin(uow)
    rows = (
        await uow.connection.execute(
            select(User.id, User.login, User.display_name, User.is_active, User.is_admin).order_by(
                User.login, User.id
            )
        )
    ).mappings()
    return tuple(UserSummary.model_validate(row) for row in rows)
