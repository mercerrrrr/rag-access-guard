from collections.abc import Iterator
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from sqlalchemy.engine import make_url

from rag_access_guard_api.config import Settings


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
