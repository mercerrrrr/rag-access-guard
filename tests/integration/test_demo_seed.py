from collections.abc import Iterator
from pathlib import Path

import anyio
import pytest
from pydantic import PostgresDsn
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import create_async_engine

from rag_access_guard_api.adapters import embeddings
from rag_access_guard_api.config import Settings
from rag_access_guard_api.demo.credentials import CredentialJournal
from rag_access_guard_api.demo.errors import DemoOwnershipError
from rag_access_guard_api.demo.files import load_private_json
from rag_access_guard_api.demo.seed import SeedOptions, seed_demo
from rag_access_guard_api.persistence import DocumentVersion, User
from rag_access_guard_api.schemas.auth import LoginRequest
from rag_access_guard_api.server import create_event_loop
from rag_access_guard_api.services.auth import AuthCredentials, AuthService
from rag_access_guard_api.services.errors import (
    ForbiddenError,
    RateLimitedError,
    UnauthenticatedError,
)
from tests.helpers.demo_seed import owned_fixture_database, seed_config
from tests.integration.embedding_fixtures import DeterministicEmbedder


@pytest.fixture
def owned_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[URL]:
    monkeypatch.setattr(embeddings, "get_embedding_adapter", DeterministicEmbedder)
    yield from owned_fixture_database(monkeypatch)


def test_first_apply_creates_distinct_accounts_in_owned_database(
    owned_database: URL, tmp_path: Path
) -> None:
    # Given: a migrated random disposable target and externally defined accounts.
    config = seed_config(tmp_path, owned_database)
    # When: the explicit first apply initializes and seeds it.
    _ = anyio.run(
        seed_demo,
        config,
        SeedOptions(apply=True, initialize_owned_database=True),
        backend_options={"loop_factory": create_event_loop},
    )
    # Then: each distinct account authenticates with its recoverable persisted credential.
    engine = create_engine(owned_database, hide_parameters=True)
    try:
        with engine.connect() as connection:
            count = connection.execute(select(func.count()).select_from(User)).scalar_one()
            assert count == 2
            hashes = connection.execute(select(User.password_hash)).scalars().all()
            assert len(set(hashes)) == 2
    finally:
        engine.dispose()

    async def authenticate_generated_accounts() -> None:
        journal = load_private_json(config.credentials_path, CredentialJournal)
        assert {entry.login for entry in journal.entries} == {"operator", "reader"}
        auth_engine = create_async_engine(owned_database, hide_parameters=True)
        try:
            auth = AuthService(
                auth_engine,
                Settings(
                    database_url=PostgresDsn(config.database_url.get_secret_value()),
                    model_profile=config.model_profile,
                ),
            )
            await auth.initialize()
            for entry in journal.entries:
                try:
                    preauth, csrf = await auth.bootstrap(AuthCredentials(), "127.0.0.1")
                    session, response = await auth.login(
                        LoginRequest(login=entry.login, password=entry.password.get_secret_value()),
                        AuthCredentials(preauth=preauth or "", csrf=csrf),
                        "127.0.0.1",
                    )
                    restored = await auth.me(session)
                except (UnauthenticatedError, ForbiddenError, RateLimitedError):
                    message = "seed journal credential did not establish an ordinary session"
                    raise AssertionError(message) from None
                assert response.user.login == restored.login == entry.login
                assert restored.is_admin == (entry.login == "operator")
        finally:
            await auth_engine.dispose()

    anyio.run(authenticate_generated_accounts, backend_options={"loop_factory": create_event_loop})


def test_apply_uses_ordinary_ingestion_for_all_documents(
    owned_database: URL, tmp_path: Path
) -> None:
    # Given: external local inputs and the existing deterministic adapter test seam.
    config = seed_config(tmp_path, owned_database)
    # When: the initial apply commits complete ready versions.
    _ = anyio.run(
        seed_demo,
        config,
        SeedOptions(apply=True, initialize_owned_database=True),
        backend_options={"loop_factory": create_event_loop},
    )
    # Then: every ordinary version is ready, with no fabricated chunk identifiers.
    engine = create_engine(owned_database, hide_parameters=True)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(
                    select(func.count())
                    .select_from(DocumentVersion)
                    .where(DocumentVersion.status == "ready")
                ).scalar_one()
                == 24
            )
    finally:
        engine.dispose()


def test_lost_original_journal_is_denied(owned_database: URL, tmp_path: Path) -> None:
    # Given: existing completed accounts and a lost private credential journal.
    config = seed_config(tmp_path, owned_database)
    _ = anyio.run(
        seed_demo,
        config,
        SeedOptions(apply=True, initialize_owned_database=True),
        backend_options={"loop_factory": create_event_loop},
    )
    config.credentials_path.unlink()
    # When/Then: resume refuses to invent replacement credentials.
    with pytest.raises(DemoOwnershipError, match="credential_missing"):
        _ = anyio.run(
            seed_demo,
            config,
            SeedOptions(apply=True),
            backend_options={"loop_factory": create_event_loop},
        )


def test_raw_first_dry_run_has_no_database_writes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: an actually unmigrated disposable target with preinstalled vector only.
    for target in owned_fixture_database(monkeypatch, migrated=False):
        config = seed_config(tmp_path, target)
        # When: the operator inspects the first plan without initialization.
        result = anyio.run(
            seed_demo, config, SeedOptions(), backend_options={"loop_factory": create_event_loop}
        )
        # Then: the plan contains every new object and leaves policy/schema absent.
        assert result.policy_revision_before is result.policy_revision_after is None
        assert len(result.plan.planned_new_objects) == 26
        engine = create_engine(target, hide_parameters=True)
        try:
            with engine.connect() as connection:
                assert (
                    connection.execute(
                        text("SELECT to_regclass('public.policy_state')")
                    ).scalar_one()
                    is None
                )
                assert (
                    connection.execute(
                        text("SELECT to_regclass('public.demo_seed_state')")
                    ).scalar_one()
                    is None
                )
            assert not config.credentials_path.exists()
            monkeypatch.setattr(embeddings, "get_embedding_adapter", DeterministicEmbedder)
            initialized = anyio.run(
                seed_demo,
                config,
                SeedOptions(apply=True, initialize_owned_database=True),
                backend_options={"loop_factory": create_event_loop},
            )
            assert initialized.counts.new_objects == 26
            assert initialized.counts.new_versions == 24
            assert initialized.policy_revision_before == 0
        finally:
            engine.dispose()


def test_populated_custom_schema_is_denied_before_migration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: a raw disposable target containing data outside the default schema.
    for target in owned_fixture_database(monkeypatch, migrated=False):
        config = seed_config(tmp_path, target)
        engine = create_engine(target, hide_parameters=True)
        try:
            with engine.begin() as connection:
                _ = connection.execute(text("CREATE SCHEMA foreign_data"))
                _ = connection.execute(text("CREATE TABLE foreign_data.payload(value integer)"))
                _ = connection.execute(text("INSERT INTO foreign_data.payload VALUES (7)"))
            # When/Then: initialization refuses it without creating application objects.
            with pytest.raises(DemoOwnershipError, match="unowned_schema"):
                _ = anyio.run(
                    seed_demo,
                    config,
                    SeedOptions(apply=True, initialize_owned_database=True),
                    backend_options={"loop_factory": create_event_loop},
                )
            with engine.connect() as connection:
                assert (
                    connection.execute(
                        text("SELECT to_regclass('public.alembic_version')")
                    ).scalar_one()
                    is None
                )
                assert (
                    connection.execute(
                        text("SELECT to_regclass('public.demo_seed_state')")
                    ).scalar_one()
                    is None
                )
                assert (
                    connection.execute(text("SELECT value FROM foreign_data.payload")).scalar_one()
                    == 7
                )
            assert not config.credentials_path.exists()
        finally:
            engine.dispose()
