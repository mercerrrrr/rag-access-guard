from collections.abc import Iterator
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import make_url

from rag_access_guard_api.config import Settings
from rag_access_guard_api.main import create_app
from rag_access_guard_api.schemas.auth import CsrfResponse
from rag_access_guard_api.server import create_event_loop
from rag_access_guard_api.services.passwords import hash_password


@pytest.fixture
def auth_client(
    isolated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("RAG_ACCESS_GUARD_DATABASE_URL", isolated_database_url)
    command.upgrade(Config("apps/api/alembic.ini"), "head")
    engine = create_engine(isolated_database_url)
    with engine.begin() as connection:
        _ = connection.execute(
            text(
                """INSERT INTO users (id, login, display_name, password_hash)
                VALUES (:id, 'reader', 'Reader', :hash)"""
            ),
            {"id": uuid4(), "hash": hash_password("Synthetic-Pass-123")},
        )
    engine.dispose()
    with TestClient(
        create_app(),
        base_url="https://rag.test",
        backend_options={"loop_factory": create_event_loop},
    ) as client:
        yield client


@pytest.fixture
def authenticated_client(auth_client: TestClient) -> TestClient:
    csrf = CsrfResponse.model_validate_json(auth_client.get("/api/auth/csrf").content).csrf_token
    response = auth_client.post(
        "/api/auth/login",
        json={"login": "reader", "password": "Synthetic-Pass-123"},
        headers={"Origin": "https://rag.test", "X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    return auth_client


@pytest.fixture
def auth_database(auth_client: TestClient) -> Iterator[Engine]:
    assert auth_client.base_url.host == "rag.test"
    engine = create_engine(str(Settings().database_url), hide_parameters=True)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def isolated_database_url() -> Iterator[str]:
    configured_url = make_url(str(Settings().database_url))
    database_name = f"rag_access_guard_test_{uuid4().hex}"
    admin_url = configured_url.set(drivername="postgresql", database="postgres")
    test_url = configured_url.set(database=database_name)

    with psycopg.connect(
        admin_url.render_as_string(hide_password=False),
        autocommit=True,
    ) as connection:
        _ = connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))

    try:
        yield test_url.render_as_string(hide_password=False)
    finally:
        with psycopg.connect(
            admin_url.render_as_string(hide_password=False),
            autocommit=True,
        ) as connection:
            _ = connection.execute(
                """
                SELECT pg_catalog.pg_terminate_backend(pid)
                FROM pg_catalog.pg_stat_activity
                WHERE datname = %s
                  AND pid <> pg_catalog.pg_backend_pid()
                """,
                (database_name,),
            )
            _ = connection.execute(
                sql.SQL("DROP DATABASE {}").format(sql.Identifier(database_name))
            )


@pytest.fixture
def schema_connection(
    isolated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Connection]:
    monkeypatch.setenv("RAG_ACCESS_GUARD_DATABASE_URL", isolated_database_url)
    command.upgrade(Config("apps/api/alembic.ini"), "head")
    engine = create_engine(isolated_database_url)
    try:
        with engine.connect() as connection, connection.begin():
            yield connection
    finally:
        engine.dispose()


@pytest.fixture
def seeded_connection(schema_connection: Connection) -> Connection:
    statements = (
        """INSERT INTO users (id, login, display_name, password_hash, is_admin)
           VALUES ('00000000-0000-0000-0000-000000000001',
                   'reader', 'Reader', 'test-hash', true)""",
        """INSERT INTO roles (id, code, display_name)
           VALUES ('00000000-0000-0000-0000-000000000002', 'engineering', 'Engineering')""",
        "INSERT INTO user_roles (user_id, role_id) SELECT users.id, roles.id FROM users, roles",
        """INSERT INTO documents (id, title, created_by)
           SELECT '00000000-0000-0000-0000-000000000003', 'Synthetic document', id FROM users""",
        """INSERT INTO document_grants (id, document_id, user_id, created_by)
           SELECT gen_random_uuid(), documents.id, users.id, users.id FROM documents, users""",
        """INSERT INTO document_grants (id, document_id, role_id, created_by)
           SELECT gen_random_uuid(), documents.id, roles.id, users.id
           FROM documents, users, roles""",
        """INSERT INTO sessions (id, user_id, token_digest, csrf_token_digest, absolute_expires_at)
           SELECT gen_random_uuid(), id, decode(repeat('ab', 32), 'hex'),
                  decode(repeat('cd', 32), 'hex'), now() + interval '8 hours' FROM users""",
        """INSERT INTO audit_events
           (id, actor_user_id, event_type, stage, outcome, policy_revision)
           SELECT gen_random_uuid(), id, 'grant_added', 'policy', 'success', 0 FROM users""",
    )
    for statement in statements:
        _ = schema_connection.execute(text(statement))
    return schema_connection
