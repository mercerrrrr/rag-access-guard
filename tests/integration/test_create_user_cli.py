import os
import re
import subprocess
import sys
from typing import Final

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select

from rag_access_guard_api.persistence import AuditEvent, PolicyState, User
from rag_access_guard_api.services.passwords import verify_password

SUBPROCESS_CREATION_FLAGS: Final = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
ANSI_CONTROL_SEQUENCE: Final[re.Pattern[str]] = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
CONTROLLED_STDIN_BOOTSTRAP: Final = (
    "import io, os, runpy, sys; "
    "sys.stdin = io.TextIOWrapper(os.fdopen(0, 'rb', closefd=False)); "
    "sys.argv = ['rag-access-guard-create-user', *sys.argv[1:]]; "
    "runpy.run_module('rag_access_guard_api.cli.create_user', run_name='__main__')"
)


def run_create_user(
    database_url: str,
    *,
    login: str,
    display_name: str,
    password: str,
    admin: bool = False,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["RAG_ACCESS_GUARD_DATABASE_URL"] = database_url
    arguments = [
        sys.executable,
        "-c",
        CONTROLLED_STDIN_BOOTSTRAP,
        "--login",
        login,
        "--display-name",
        display_name,
    ]
    if admin:
        arguments.append("--admin")
    return subprocess.run(  # noqa: S603 -- fixed interpreter and module, no shell
        arguments,
        check=False,
        capture_output=True,
        input=f"{password}\n{password}\n",
        text=True,
        env=environment,
        creationflags=SUBPROCESS_CREATION_FLAGS,
        timeout=15,
    )


def test_create_user_cli_reads_password_without_echo(
    isolated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a migrated disposable database and password available only on stdin.
    monkeypatch.setenv("RAG_ACCESS_GUARD_DATABASE_URL", isolated_database_url)
    command.upgrade(Config("apps/api/alembic.ini"), "head")
    password = "Synthetic-CLI-Pass-123"  # noqa: S105 -- synthetic test credential

    # When: the real CLI process creates a normalized administrator account.
    result = run_create_user(
        isolated_database_url,
        login="  Operator  ",
        display_name="Local Operator",
        password=password,
        admin=True,
    )

    # Then: output contains no password/hash and the policy mutation is atomic.
    assert result.returncode == 0, result.stderr
    assert password not in result.stdout
    assert password not in result.stderr
    created_id, output_login = result.stdout.strip().split()
    assert output_login == "operator"

    engine = create_engine(isolated_database_url)
    try:
        with engine.connect() as connection:
            user_id = connection.execute(
                select(User.id).where(User.login == "operator")
            ).scalar_one()
            user_login = connection.execute(
                select(User.login).where(User.login == "operator")
            ).scalar_one()
            display_name = connection.execute(
                select(User.display_name).where(User.login == "operator")
            ).scalar_one()
            password_hash = connection.execute(
                select(User.password_hash).where(User.login == "operator")
            ).scalar_one()
            is_admin = connection.execute(
                select(User.is_admin).where(User.login == "operator")
            ).scalar_one()
            audit_actor_id = connection.execute(select(AuditEvent.actor_user_id)).scalar_one()
            audit_principal_id = connection.execute(select(AuditEvent.principal_id)).scalar_one()
            audit_event_type = connection.execute(select(AuditEvent.event_type)).scalar_one()
            audit_stage = connection.execute(select(AuditEvent.stage)).scalar_one()
            audit_outcome = connection.execute(select(AuditEvent.outcome)).scalar_one()
            audit_revision = connection.execute(select(AuditEvent.policy_revision)).scalar_one()
            audit_source_count = connection.execute(select(AuditEvent.source_count)).scalar_one()
            revision = connection.execute(
                select(PolicyState.revision).where(PolicyState.id == 1)
            ).scalar_one()
    finally:
        engine.dispose()

    assert str(user_id) == created_id
    assert user_login == "operator"
    assert display_name == "Local Operator"
    assert verify_password(password, password_hash)
    assert is_admin is True
    assert audit_actor_id is None
    assert str(audit_principal_id) == created_id
    assert audit_event_type == "user_changed"
    assert audit_stage == "policy"
    assert audit_outcome == "success"
    assert audit_revision == 1
    assert audit_source_count == 0
    assert revision == 1


def test_create_user_cli_duplicate_does_not_overwrite(
    isolated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an existing account created through the real CLI.
    monkeypatch.setenv("RAG_ACCESS_GUARD_DATABASE_URL", isolated_database_url)
    command.upgrade(Config("apps/api/alembic.ini"), "head")
    original_password = "Synthetic-Original-123"  # noqa: S105 -- synthetic test credential
    first = run_create_user(
        isolated_database_url,
        login="operator",
        display_name="Original Name",
        password=original_password,
    )
    assert first.returncode == 0, first.stderr

    # When: a second process attempts the same normalized login with different values.
    duplicate_password = "Synthetic-Replacement-456"  # noqa: S105 -- synthetic test credential
    duplicate = run_create_user(
        isolated_database_url,
        login=" OPERATOR ",
        display_name="Replacement Name",
        password=duplicate_password,
        admin=True,
    )

    # Then: it fails without changing the account or adding policy side effects.
    assert duplicate.returncode != 0
    assert original_password not in duplicate.stderr
    assert duplicate_password not in duplicate.stderr
    engine = create_engine(isolated_database_url)
    try:
        with engine.connect() as connection:
            display_name = connection.execute(
                select(User.display_name).where(User.login == "operator")
            ).scalar_one()
            password_hash = connection.execute(
                select(User.password_hash).where(User.login == "operator")
            ).scalar_one()
            is_admin = connection.execute(
                select(User.is_admin).where(User.login == "operator")
            ).scalar_one()
            revision = connection.execute(
                select(PolicyState.revision).where(PolicyState.id == 1)
            ).scalar_one()
            audit_count = connection.execute(
                select(func.count()).select_from(AuditEvent)
            ).scalar_one()
            user_count = connection.execute(
                select(func.count()).select_from(User).where(User.login == "operator")
            ).scalar_one()
    finally:
        engine.dispose()

    assert display_name == "Original Name"
    assert verify_password(original_password, password_hash)
    assert not verify_password(duplicate_password, password_hash)
    assert is_admin is False
    assert (revision, audit_count, user_count) == (1, 1, 1)


def test_create_user_cli_help_needs_no_runtime_configuration() -> None:
    # Given: deliberately invalid runtime configuration.
    environment = os.environ.copy()
    _ = environment.pop("RAG_ACCESS_GUARD_AUTH_LIMIT_SECRET", None)
    environment["RAG_ACCESS_GUARD_DATABASE_URL"] = "not-a-database-url"

    # When: the CLI help is requested in a real process.
    result = subprocess.run(
        [sys.executable, "-m", "rag_access_guard_api.cli.create_user", "--help"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        creationflags=SUBPROCESS_CREATION_FLAGS,
        timeout=15,
    )

    # Then: argument documentation is available without loading runtime settings.
    assert result.returncode == 0, result.stderr
    rendered_help = ANSI_CONTROL_SEQUENCE.sub("", result.stdout)
    assert "--login" in rendered_help
    assert "--display-name" in rendered_help
    assert "--admin" in rendered_help


def test_create_user_cli_database_failure_does_not_expose_password() -> None:
    # Given: a valid command targeting a database endpoint that cannot connect.
    environment = os.environ.copy()
    environment["RAG_ACCESS_GUARD_DATABASE_URL"] = (
        "postgresql+psycopg://synthetic:synthetic@127.0.0.1:1/unavailable"
    )
    password = "Synthetic-Diagnostic-Secret-789"  # noqa: S105 -- synthetic test credential

    # When: the real process reaches a database failure after both password prompts.
    result = subprocess.run(  # noqa: S603 -- fixed interpreter and inline bootstrap
        [
            sys.executable,
            "-c",
            CONTROLLED_STDIN_BOOTSTRAP,
            "--login",
            "operator",
            "--display-name",
            "Operator",
        ],
        check=False,
        capture_output=True,
        input=f"{password}\n{password}\n",
        text=True,
        env=environment,
        creationflags=SUBPROCESS_CREATION_FLAGS,
        timeout=15,
    )

    # Then: diagnostics fail closed without rendering entered credentials or rich locals.
    assert result.returncode != 0
    assert password not in result.stdout
    assert password not in result.stderr
