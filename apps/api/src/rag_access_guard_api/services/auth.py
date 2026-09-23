"""Server session lifecycle with short policy locks and offloaded passwords."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from anyio.to_thread import run_sync
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from rag_access_guard_api.config import Settings
from rag_access_guard_api.persistence import Session, User
from rag_access_guard_api.schemas.auth import LoginRequest, LoginResponse, UserView
from rag_access_guard_api.services import passwords
from rag_access_guard_api.services.audit import AuditRecord, write_audit
from rag_access_guard_api.services.auth_limits import AuthLimits
from rag_access_guard_api.services.challenges import consume_challenge, create_challenge
from rag_access_guard_api.services.errors import (
    AlreadyAuthenticatedError,
    ForbiddenError,
    UnauthenticatedError,
)
from rag_access_guard_api.services.security import PolicyUnitOfWork, database_clock, lock_policy
from rag_access_guard_api.services.tokens import issue_token, matches_token, token_digest


@dataclass(frozen=True, slots=True)
class AuthCredentials:
    """Credentials extracted exclusively from protocol cookies and headers."""

    session: str = ""
    preauth: str = ""
    csrf: str = ""


class AuthService:
    """Coordinate preflight, password verification and fresh identity gates."""

    def __init__(self, engine: AsyncEngine, settings: Settings) -> None:
        """Keep dependencies local to an application instance."""
        self.engine: AsyncEngine = engine
        self.policy: PolicyUnitOfWork = PolicyUnitOfWork(engine)
        self.limits: AuthLimits = AuthLimits(
            engine, bytes.fromhex(settings.auth_limit_secret.get_secret_value())
        )
        self._dummy_hash: str = ""

    async def initialize(self) -> None:
        """Prepare one process-local dummy hash without blocking the event loop."""
        self._dummy_hash = await run_sync(passwords.hash_password, issue_token())

    @asynccontextmanager
    async def _authentication_scope(self) -> AsyncGenerator[tuple[AsyncConnection, int]]:
        async with self.engine.begin() as connection:
            revision = await lock_policy(connection, exclusive=False)
            yield connection, revision

    async def bootstrap(self, credentials: AuthCredentials, peer: str) -> tuple[str | None, str]:
        """Preserve healthy session CSRF or create an unprivileged challenge."""
        await self.limits.reserve(peer)
        try:
            async with self.policy.protected_read(credentials.session) as uow:
                if matches_token(credentials.csrf, uow.csrf_digest):
                    return None, credentials.csrf
                csrf = issue_token()
                _ = await uow.connection.execute(
                    update(Session)
                    .where(Session.id == uow.principal.session_id)
                    .values(csrf_token_digest=token_digest(csrf))
                )
                return None, csrf
        except UnauthenticatedError:
            return await create_challenge(self.engine)

    async def login(
        self, request: LoginRequest, credentials: AuthCredentials, peer: str
    ) -> tuple[str, LoginResponse]:
        """Consume pre-auth, verify unlocked, then re-read active user and hash."""
        try:
            async with self.policy.protected_read(credentials.session):
                raise AlreadyAuthenticatedError
        except UnauthenticatedError:
            pass
        await self.limits.reserve(peer, request.login)
        await consume_challenge(self.engine, credentials.preauth, credentials.csrf)
        async with self.engine.connect() as connection:
            snapshot = (
                (
                    await connection.execute(
                        select(User.id, User.password_hash).where(User.login == request.login)
                    )
                )
                .tuples()
                .one_or_none()
            )
        encoded = self._dummy_hash if snapshot is None else snapshot[1]
        verified = await run_sync(passwords.verify_password, request.password, encoded)
        async with self._authentication_scope() as (connection, revision):
            user = (
                None
                if snapshot is None
                else (
                    await connection.execute(
                        select(
                            User.id,
                            User.login,
                            User.display_name,
                            User.is_admin,
                            User.is_active,
                            User.password_hash,
                        ).where(User.id == snapshot[0])
                    )
                )
                .tuples()
                .one_or_none()
            )
            if not verified or user is None or not user[4] or user[5] != encoded:
                await write_audit(
                    connection,
                    AuditRecord(
                        event_type="login_denied", stage="authentication", outcome="denied"
                    ),
                    revision,
                )
                result = None
            else:
                session, csrf = issue_token(), issue_token()
                now = await database_clock(connection)
                _ = await connection.execute(
                    insert(Session).values(
                        id=uuid4(),
                        user_id=user[0],
                        token_digest=token_digest(session),
                        csrf_token_digest=token_digest(csrf),
                        created_at=now,
                        last_seen_at=now,
                        absolute_expires_at=now + timedelta(hours=8),
                    )
                )
                await write_audit(
                    connection,
                    AuditRecord(
                        event_type="session_created",
                        stage="authentication",
                        outcome="success",
                        actor_user_id=user[0],
                        principal_id=user[0],
                    ),
                    revision,
                )
                result = (
                    session,
                    LoginResponse(
                        user=UserView(
                            id=user[0],
                            login=user[1],
                            display_name=user[2],
                            is_admin=user[3],
                        ),
                        csrf_token=csrf,
                    ),
                )
        if result is None:
            raise UnauthenticatedError
        return result

    async def me(self, session: str) -> UserView:
        """Restore only a presently valid session identity."""
        async with self.policy.protected_read(session) as uow:
            return uow.user

    async def logout(self, credentials: AuthCredentials) -> None:
        """Check CSRF inside the same exclusive UoW that revokes the session."""
        async with self.policy.mutation(credentials.session) as uow:
            if not matches_token(credentials.csrf, uow.csrf_digest):
                raise ForbiddenError
            _ = await uow.connection.execute(
                update(Session)
                .where(Session.id == uow.principal.session_id)
                .values(revoked_at=await database_clock(uow.connection))
            )
            _ = await uow.record_change(
                AuditRecord(
                    event_type="session_revoked",
                    stage="authentication",
                    outcome="success",
                    actor_user_id=uow.principal.principal_id,
                    principal_id=uow.principal.principal_id,
                )
            )
