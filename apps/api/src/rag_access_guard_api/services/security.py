"""Policy-first transactional session gates and atomic security mutations."""

import hmac
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import DateTime, func, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from rag_access_guard_api.persistence import PolicyState, Session, User
from rag_access_guard_api.schemas.auth import SessionPrincipal, UserView
from rag_access_guard_api.services.audit import AuditRecord, write_audit
from rag_access_guard_api.services.errors import UnauthenticatedError
from rag_access_guard_api.services.tokens import token_digest


async def database_clock(connection: AsyncConnection) -> datetime:
    """Read wall-clock time after all authorization lock waits."""
    return (
        await connection.execute(select(func.clock_timestamp(type_=DateTime(timezone=True))))
    ).scalar_one()


def session_is_current(now: datetime, last_seen: datetime, absolute_expiry: datetime) -> bool:
    """Deny equality at either absolute or idle expiry boundary."""
    return now < absolute_expiry and now < last_seen + timedelta(minutes=30)


@dataclass(frozen=True, slots=True)
class ReadUoW:
    """A live transaction and the freshly validated identity it protects."""

    connection: AsyncConnection
    principal: SessionPrincipal
    revision: int
    user: UserView
    csrf_digest: bytes


class MutationUoW:
    """Mutable revision accumulator for exactly one effective security change."""

    def __init__(self, read: ReadUoW) -> None:
        """Promote an already gated transaction holding the exclusive policy lock."""
        self.connection: AsyncConnection = read.connection
        self.principal: SessionPrincipal = read.principal
        self.revision: int = read.revision
        self.csrf_digest: bytes = read.csrf_digest
        self._changed: bool = False

    async def record_change(self, event: AuditRecord) -> int:
        """Increment once, writing every event atomically with that revision."""
        if not self._changed:
            self.revision = (
                await self.connection.execute(
                    update(PolicyState)
                    .where(PolicyState.id == 1)
                    .values(revision=PolicyState.revision + 1, updated_at=func.clock_timestamp())
                    .returning(PolicyState.revision)
                )
            ).scalar_one()
            self._changed = True
        await write_audit(self.connection, event, self.revision)
        return self.revision


async def _gate(connection: AsyncConnection, digest: bytes, revision: int) -> ReadUoW:
    session = (
        (
            await connection.execute(
                select(
                    Session.id,
                    Session.user_id,
                    Session.csrf_token_digest,
                    Session.last_seen_at,
                    Session.absolute_expires_at,
                    Session.revoked_at,
                    Session.token_digest,
                )
                .where(Session.token_digest == digest)
                .with_for_update()
            )
        )
        .tuples()
        .one_or_none()
    )
    if session is None:
        raise UnauthenticatedError
    user = (
        (
            await connection.execute(
                select(User.id, User.login, User.display_name, User.is_admin, User.is_active).where(
                    User.id == session[1]
                )
            )
        )
        .tuples()
        .one()
    )
    now = await database_clock(connection)
    if (
        not user[4]
        or session[5] is not None
        or not hmac.compare_digest(digest, session[6])
        or not session_is_current(now, session[3], session[4])
    ):
        raise UnauthenticatedError
    _ = await connection.execute(
        update(Session).where(Session.id == session[0]).values(last_seen_at=now)
    )
    return ReadUoW(
        connection,
        SessionPrincipal(user[0], session[0], user[3]),
        revision,
        UserView(id=user[0], login=user[1], display_name=user[2], is_admin=user[3]),
        session[2],
    )


class PolicyUnitOfWork:
    """Every protected operation reacquires policy, session and user state."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Bind all security work to the same PostgreSQL engine."""
        self._engine: AsyncEngine = engine

    @asynccontextmanager
    async def protected_read(self, session_token: str) -> AsyncGenerator[ReadUoW]:
        """Commit a protected read only after its body succeeds."""
        digest = token_digest(session_token)
        if digest is None:
            raise UnauthenticatedError
        async with self._engine.begin() as connection:
            revision = (
                await connection.execute(
                    select(PolicyState.revision)
                    .where(PolicyState.id == 1)
                    .with_for_update(read=True)
                )
            ).scalar_one()
            yield await _gate(connection, digest, revision)

    @asynccontextmanager
    async def mutation(self, session_token: str) -> AsyncGenerator[MutationUoW]:
        """Acquire exclusive policy lock before any session or policy query."""
        digest = token_digest(session_token)
        if digest is None:
            raise UnauthenticatedError
        async with self._engine.begin() as connection:
            revision = (
                await connection.execute(
                    select(PolicyState.revision).where(PolicyState.id == 1).with_for_update()
                )
            ).scalar_one()
            yield MutationUoW(await _gate(connection, digest, revision))
