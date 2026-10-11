"""Durable credentials precede atomic account/ledger commits and ordinary authentication."""

from dataclasses import replace

from anyio.to_thread import run_sync
from pydantic import PostgresDsn
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from rag_access_guard_api.config import Settings
from rag_access_guard_api.demo.access import SeedUser
from rag_access_guard_api.demo.contracts import DemoConfig
from rag_access_guard_api.demo.credentials import CredentialEntry, CredentialJournal
from rag_access_guard_api.demo.errors import DemoOwnershipError
from rag_access_guard_api.demo.ledger import complete_step, lock_marker, read_ledger
from rag_access_guard_api.demo.plan import SeedBinding, definition_hash
from rag_access_guard_api.persistence import User
from rag_access_guard_api.schemas.auth import LoginRequest
from rag_access_guard_api.services.auth import AuthCredentials, AuthService
from rag_access_guard_api.services.passwords import hash_password
from rag_access_guard_api.services.security import lock_policy
from rag_access_guard_api.services.user_creation import CreateUserRequest, insert_user_record


def credential_for(journal: CredentialJournal, login: str) -> CredentialEntry:
    """Recover the persisted credential; missing originals are never regenerated."""
    entry = next((entry for entry in journal.entries if entry.login == login), None)
    if entry is None:
        raise DemoOwnershipError(code="credential_missing")
    return entry


async def create_seed_user(engine: AsyncEngine, user: SeedUser, journal: CredentialJournal) -> bool:
    """Hash off-lock, then recheck completion and atomically insert a trusted local account."""
    marker = journal.marker
    password_hash = await run_sync(
        hash_password, credential_for(journal, user.login).password.get_secret_value()
    )
    binding = SeedBinding(
        f"user:{user.login}", marker.instance_id, definition_sha256=definition_hash(user)
    )
    async with engine.begin() as connection:
        _ = await lock_policy(connection, exclusive=True)
        await lock_marker(connection, marker)
        ledger = await read_ledger(connection, marker)
        tracked = next((item for item in ledger.bindings if item.key == binding.key), None)
        current = (
            await connection.execute(select(User.id).where(User.login == user.login))
        ).scalar_one_or_none()
        if tracked is not None:
            if (
                current != tracked.object_id
                or tracked.definition_sha256 != binding.definition_sha256
            ):
                raise DemoOwnershipError(code="tracked_user_drift")
            return False
        if current is not None:
            raise DemoOwnershipError(code="untracked_user_drift")
        identifier = await insert_user_record(
            connection,
            CreateUserRequest(
                login=user.login,
                display_name=user.display_name,
                password_hash=password_hash,
                is_admin=user.is_admin,
            ),
        )
        await complete_step(connection, marker, replace(binding, object_id=identifier))
    return True


async def authenticate_operator(
    engine: AsyncEngine, config: DemoConfig, credential: CredentialEntry
) -> tuple[AuthService, str, str]:
    """Use the existing challenge/password/session flow without a fabricated principal."""
    settings = Settings(
        database_url=PostgresDsn(config.database_url.get_secret_value()),
        model_profile=config.model_profile,
    )
    auth = AuthService(engine, settings)
    await auth.initialize()
    preauth, csrf = await auth.bootstrap(AuthCredentials(), "127.0.0.1")
    session, principal = await auth.login(
        LoginRequest(login=credential.login, password=credential.password.get_secret_value()),
        AuthCredentials(preauth=preauth or "", csrf=csrf),
        "127.0.0.1",
    )
    return auth, session, principal.csrf_token
