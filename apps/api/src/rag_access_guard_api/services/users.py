"""Administrative identity changes serialized with protected reads."""

from uuid import UUID

from sqlalchemy import select, update

from rag_access_guard_api.persistence import Session, User
from rag_access_guard_api.schemas.access import UserSummary
from rag_access_guard_api.services.audit import AuditRecord
from rag_access_guard_api.services.documents import require_admin
from rag_access_guard_api.services.security import MutationUoW, database_clock
from rag_access_guard_api.services.text_documents import DocumentError


async def update_user_security(
    uow: MutationUoW, user_id: UUID, *, is_active: bool | None = None, is_admin: bool | None = None
) -> UserSummary:
    """Change security flags without modifying credentials or independent grants."""
    require_admin(uow)
    if is_active is None and is_admin is None:
        raise DocumentError(422)
    columns = (User.id, User.login, User.display_name, User.is_active, User.is_admin)
    row = (
        (await uow.connection.execute(select(*columns).where(User.id == user_id).with_for_update()))
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise DocumentError(404)
    current = UserSummary.model_validate(row)
    active = current.is_active if is_active is None else is_active
    admin = current.is_admin if is_admin is None else is_admin
    if (active, admin) == (current.is_active, current.is_admin):
        return current
    changed = (
        (
            await uow.connection.execute(
                update(User)
                .where(User.id == user_id)
                .values(is_active=active, is_admin=admin)
                .returning(*columns)
            )
        )
        .mappings()
        .one()
    )
    _ = await uow.record_change(
        AuditRecord(
            event_type="user_changed",
            stage="policy",
            outcome="success",
            actor_user_id=uow.principal.principal_id,
            principal_id=user_id,
        )
    )
    return UserSummary.model_validate(changed)


async def revoke_user_session(uow: MutationUoW, user_id: UUID, session_id: UUID) -> None:
    """Revoke only the named user's session; repeated revocation is a no-op."""
    require_admin(uow)
    row = (
        (
            await uow.connection.execute(
                select(Session.id, Session.revoked_at)
                .where(Session.id == session_id, Session.user_id == user_id)
                .with_for_update()
            )
        )
        .tuples()
        .one_or_none()
    )
    if row is None:
        raise DocumentError(404)
    if row[1] is not None:
        return
    _ = await uow.connection.execute(
        update(Session)
        .where(Session.id == session_id)
        .values(revoked_at=await database_clock(uow.connection))
    )
    _ = await uow.record_change(
        AuditRecord(
            event_type="session_revoked",
            stage="authentication",
            outcome="success",
            actor_user_id=uow.principal.principal_id,
            principal_id=user_id,
        )
    )
