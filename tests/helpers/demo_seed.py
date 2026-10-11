import os
import secrets
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from pydantic import SecretStr
from sqlalchemy.engine import URL, make_url

from rag_access_guard_api.demo import ownership
from rag_access_guard_api.demo.access import (
    AccessDefinition,
    SeedGrant,
    SeedMembership,
    SeedRole,
    SeedUser,
)
from rag_access_guard_api.demo.contracts import DemoConfig, DemoMarker, DemoRuntime
from tests.helpers.demo_corpus_factory import corpus_fixture


def owned_fixture_database(
    monkeypatch: pytest.MonkeyPatch, *, migrated: bool = True
) -> Iterator[URL]:
    admin_url = make_url(os.environ["RAG_E2E_DATABASE_ADMIN_URL"]).set(
        drivername="postgresql", database="postgres"
    )
    assert admin_url.host in {"localhost", "127.0.0.1", "::1"}
    assert admin_url.port == 55466
    assert not admin_url.query
    name, role = f"rag_access_guard_test_{uuid4().hex}", f"rag_demo_role_{uuid4().hex}"
    secret = secrets.token_urlsafe(24)
    target = admin_url.set(
        database=name, username=role, password=secret, drivername="postgresql+psycopg"
    )
    created_role, created_database = False, False
    try:
        with psycopg.connect(
            admin_url.render_as_string(hide_password=False), autocommit=True
        ) as connection:
            _ = connection.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(secret)
                )
            )
            created_role = True
            _ = connection.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(name), sql.Identifier(role)
                )
            )
            created_database = True
        with psycopg.connect(
            admin_url.set(database=name).render_as_string(hide_password=False), autocommit=True
        ) as connection:
            _ = connection.execute("CREATE EXTENSION vector VERSION '0.8.6'")
        monkeypatch.setenv(
            "RAG_ACCESS_GUARD_DATABASE_URL", target.render_as_string(hide_password=False)
        )
        if migrated:
            command.upgrade(Config("apps/api/alembic.ini"), "head")

        def fixture_locator(config: DemoConfig) -> URL:
            candidate = make_url(config.database_url.get_secret_value())
            assert candidate == target
            return candidate

        monkeypatch.setattr(ownership, "validate_target_locator", fixture_locator)
        yield target
    finally:
        with psycopg.connect(
            admin_url.render_as_string(hide_password=False), autocommit=True
        ) as connection:
            if created_database:
                _ = connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
            if created_role:
                _ = connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def seed_config(root: Path, target: URL) -> DemoConfig:
    manifest = corpus_fixture(root / "corpus")
    marker = DemoMarker(
        instance_id=uuid4(),
        dataset_id="neutral-corpus",
        database_name=target.database or "",
        database_role=target.username or "",
    )
    _ = (root / "owner.json").write_text(marker.model_dump_json(), encoding="utf-8")
    access = AccessDefinition(
        schema_version=1,
        users=(
            SeedUser(login="operator", display_name="Operator", is_admin=True),
            SeedUser(login="reader", display_name="Reader", is_admin=False),
        ),
        roles=(),
        memberships=(),
        grants=(),
    )
    _ = (root / "access.json").write_text(access.model_dump_json(), encoding="utf-8")
    return DemoConfig(
        dataset_id="neutral-corpus",
        database_url=SecretStr(target.render_as_string(hide_password=False)),
        corpus_manifest=manifest,
        ownership_manifest=root / "owner.json",
        credentials_path=root / "private" / "credentials.json",
        access_definition=root / "access.json",
        model_profile="qwen3-instruct-demo-v1",
        retrieval_config=root / "retrieval.json",
        runtime=DemoRuntime(
            repository_root=root,
            state_directory=root,
            log_directory=root,
            ollama_supervisor_config=root / "ollama.json",
        ),
    )


def matrix_access() -> AccessDefinition:
    users = tuple(
        SeedUser(
            login="operator" if index == 0 else f"reader{index}",
            display_name=f"Account {index}",
            is_admin=index == 0,
        )
        for index in range(5)
    )
    roles = tuple(
        SeedRole(
            code="reader1" if index == 0 else f"scope{index}",
            service_code=f"scope_{index}",
            display_name=f"Scope {index}",
        )
        for index in range(3)
    )
    memberships = tuple(
        SeedMembership(user_login=users[index % 3 + 1].login, role_code=roles[index // 3].code)
        for index in range(9)
    )
    grants = (
        tuple(
            SeedGrant(subject_kind="user", subject_key=user.login, document_key=f"item-{document}")
            for user in users
            if not user.is_admin
            for document in range(5)
        )
        + tuple(
            SeedGrant(subject_kind="role", subject_key=role.code, document_key=f"item-{document}")
            for role in roles
            for document in range(5)
        )
        + tuple(
            SeedGrant(subject_kind="user", subject_key="reader1", document_key=f"item-{index}")
            for index in range(5, 10)
        )
    )
    return AccessDefinition(
        schema_version=1, users=users, roles=roles, memberships=memberships, grants=grants
    )
