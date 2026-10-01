from collections.abc import Generator
from contextlib import contextmanager
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from sqlalchemy import create_engine, insert
from sqlalchemy.engine import URL, make_url

from rag_access_guard_api.persistence import User
from rag_access_guard_api.services.passwords import hash_password

ACTORS = ("student", "teacher", "staff", "admin")


def validate_database_url(value: str) -> URL:
    url = make_url(value)
    if (
        url.drivername != "postgresql+psycopg"
        or url.host not in {"127.0.0.1", "localhost", "::1"}
        or url.port is None
        or url.database != "postgres"
        or url.query
    ):
        message = "E2E requires explicit loopback PostgreSQL administration database"
        raise ValueError(message)
    return url


@contextmanager
def disposable_database(value: str) -> Generator[str]:
    admin_url = validate_database_url(value)
    database_name = f"rag_access_guard_e2e_{uuid4().hex}"
    connection_string = admin_url.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(connection_string, autocommit=True) as connection:
        _ = connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    try:
        yield admin_url.set(database=database_name).render_as_string(hide_password=False)
    finally:
        with psycopg.connect(connection_string, autocommit=True) as connection:
            _ = connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database_name))
            )


def seed_users(database_url: str, password: str) -> dict[str, UUID]:
    engine = create_engine(database_url, hide_parameters=True)
    actors = {name: uuid4() for name in ACTORS}
    try:
        with engine.begin() as connection:
            for name, user_id in actors.items():
                _ = connection.execute(
                    insert(User).values(
                        id=user_id,
                        login=name,
                        display_name=f"Synthetic {name}",
                        password_hash=hash_password(password),
                        is_admin=name == "admin",
                    )
                )
    finally:
        engine.dispose()
    return actors
