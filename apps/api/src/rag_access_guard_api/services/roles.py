"""Role metadata and membership changes under the shared policy protocol."""

from uuid import UUID, uuid4

from sqlalchemy import delete, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from rag_access_guard_api.persistence import Role, User, UserRole
from rag_access_guard_api.schemas.access import RoleView, UserSummary
from rag_access_guard_api.services.audit import AuditRecord
from rag_access_guard_api.services.documents import require_admin
from rag_access_guard_api.services.errors import RoleConflictError
from rag_access_guard_api.services.security import MutationUoW, ReadUoW
from rag_access_guard_api.services.text_documents import DocumentError


async def create_role(uow: MutationUoW, *, code: str, display_name: str) -> RoleView:
    """Create an immutable code and record one effective policy change."""
    require_admin(uow)
    if (
        await uow.connection.execute(select(Role.id).where(Role.code == code))
    ).scalar_one_or_none() is not None:
        raise RoleConflictError
    row = (
        (
            await uow.connection.execute(
                insert(Role)
                .values(id=uuid4(), code=code, display_name=display_name)
                .returning(Role.id, Role.code, Role.display_name)
            )
        )
        .mappings()
        .one()
    )
    role = RoleView.model_validate(row)
    _ = await uow.record_change(
        AuditRecord(
            event_type="role_changed",
            stage="policy",
            outcome="success",
            actor_user_id=uow.principal.principal_id,
            role_id=role.id,
        )
    )
    return role


async def update_role(uow: MutationUoW, role_id: UUID, display_name: str) -> RoleView:
    """Change only the label; identical labels leave policy history untouched."""
    require_admin(uow)
    row = (
        (
            await uow.connection.execute(
                select(Role.id, Role.code, Role.display_name)
                .where(Role.id == role_id)
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise DocumentError(404)
    role = RoleView.model_validate(row)
    if role.display_name == display_name:
        return role
    changed = (
        (
            await uow.connection.execute(
                update(Role)
                .where(Role.id == role_id)
                .values(display_name=display_name)
                .returning(Role.id, Role.code, Role.display_name)
            )
        )
        .mappings()
        .one()
    )
    _ = await uow.record_change(
        AuditRecord(
            event_type="role_changed",
            stage="policy",
            outcome="success",
            actor_user_id=uow.principal.principal_id,
            role_id=role_id,
        )
    )
    return RoleView.model_validate(changed)


async def list_roles(uow: ReadUoW) -> tuple[RoleView, ...]:
    """Return administrative role metadata in stable order."""
    require_admin(uow)
    rows = (
        await uow.connection.execute(
            select(Role.id, Role.code, Role.display_name).order_by(Role.code, Role.id)
        )
    ).mappings()
    return tuple(RoleView.model_validate(row) for row in rows)


async def list_members(uow: ReadUoW, role_id: UUID) -> tuple[UserSummary, ...]:
    """Select member metadata without fetching credentials."""
    require_admin(uow)
    if (
        await uow.connection.execute(select(Role.id).where(Role.id == role_id))
    ).scalar_one_or_none() is None:
        raise DocumentError(404)
    rows = (
        await uow.connection.execute(
            select(User.id, User.login, User.display_name, User.is_active, User.is_admin)
            .join(UserRole, UserRole.user_id == User.id)
            .where(UserRole.role_id == role_id)
            .order_by(User.login, User.id)
        )
    ).mappings()
    return tuple(UserSummary.model_validate(row) for row in rows)


async def set_membership(uow: MutationUoW, role_id: UUID, user_id: UUID, *, present: bool) -> None:
    """Apply an idempotent membership change after policy/session/resource locks."""
    require_admin(uow)
    for identifier, model in sorted(((role_id, Role), (user_id, User)), key=lambda item: item[0]):
        if (
            await uow.connection.execute(
                select(model.id).where(model.id == identifier).with_for_update()
            )
        ).scalar_one_or_none() is None:
            raise DocumentError(404)
    if present:
        changed = (
            await uow.connection.execute(
                pg_insert(UserRole)
                .values(user_id=user_id, role_id=role_id)
                .on_conflict_do_nothing(index_elements=[UserRole.user_id, UserRole.role_id])
                .returning(UserRole.user_id)
            )
        ).scalar_one_or_none()
    else:
        changed = (
            await uow.connection.execute(
                delete(UserRole)
                .where(UserRole.user_id == user_id, UserRole.role_id == role_id)
                .returning(UserRole.user_id)
            )
        ).scalar_one_or_none()
    if changed is not None:
        _ = await uow.record_change(
            AuditRecord(
                event_type="membership_added" if present else "membership_removed",
                stage="policy",
                outcome="success",
                actor_user_id=uow.principal.principal_id,
                principal_id=user_id,
                role_id=role_id,
            )
        )
