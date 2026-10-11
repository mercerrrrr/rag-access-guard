"""Transaction-scoped trusted local account insertion shared with create-user."""

from dataclasses import dataclass, field
from uuid import UUID, uuid4

from sqlalchemy import func, insert, update
from sqlalchemy.ext.asyncio import AsyncConnection

from rag_access_guard_api.persistence import PolicyState, User
from rag_access_guard_api.services.audit import AuditRecord, write_audit


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateUserRequest:
    """Normalized local account inputs; the caller hashes before acquiring policy locks."""

    login: str
    display_name: str
    password_hash: str = field(repr=False)
    is_admin: bool


async def insert_user_record(connection: AsyncConnection, request: CreateUserRequest) -> UUID:
    """Insert under a caller-owned exclusive policy transaction without committing it."""
    user_id = uuid4()
    _ = await connection.execute(
        insert(User).values(
            id=user_id,
            login=request.login,
            display_name=request.display_name,
            password_hash=request.password_hash,
            is_admin=request.is_admin,
        )
    )
    revision = (
        await connection.execute(
            update(PolicyState)
            .where(PolicyState.id == 1)
            .values(revision=PolicyState.revision + 1, updated_at=func.clock_timestamp())
            .returning(PolicyState.revision)
        )
    ).scalar_one()
    await write_audit(
        connection,
        AuditRecord(
            event_type="user_changed", stage="policy", outcome="success", principal_id=user_id
        ),
        revision,
    )
    return user_id
