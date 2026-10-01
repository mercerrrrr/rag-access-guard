"""Owned loopback PostgreSQL databases for isolated paired experiments."""

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy.engine import URL, make_url


@dataclass
class ExperimentDatabases:
    """Create and remove only random databases allocated by this instance."""

    admin_url: str = field(repr=False)
    owned: set[str] = field(default_factory=set, init=False)

    def url(self) -> URL:
        """Require an explicit loopback administration database, never application data."""
        value = make_url(self.admin_url)
        if (
            value.drivername != "postgresql+psycopg"
            or value.host not in {"127.0.0.1", "localhost", "::1"}
            or value.port is None
            or value.database != "postgres"
            or value.query
        ):
            message = "Experiments require an explicit loopback PostgreSQL administration URL"
            raise ValueError(message)
        return value

    def create(self, template: str | None) -> tuple[str, str]:
        """Clone only an owned template or allocate an empty isolated database."""
        url = self.url()
        if template is not None and template not in self.owned:
            message = "Experiment template is not owned"
            raise ValueError(message)
        name = f"rag_guard_experiment_{uuid4().hex}"
        admin = url.set(drivername="postgresql").render_as_string(hide_password=False)
        with psycopg.connect(admin, autocommit=True) as connection:
            query = sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name))
            if template is not None:
                query += sql.SQL(" TEMPLATE {}").format(sql.Identifier(template))
            _ = connection.execute(query)
        self.owned.add(name)
        return name, url.set(database=name).render_as_string(hide_password=False)

    def remove(self, name: str) -> None:
        """Remove exactly an owned disposable database after its engine closes."""
        if name not in self.owned:
            message = "Refusing to remove an unowned database"
            raise ValueError(message)
        admin = self.url().set(drivername="postgresql").render_as_string(hide_password=False)
        with psycopg.connect(admin, autocommit=True) as connection:
            _ = connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
            )
        self.owned.remove(name)

    @asynccontextmanager
    async def database(self, *, template: str | None = None) -> AsyncGenerator[tuple[str, str]]:
        """Bound disposable database lifetime without modifying process environment."""
        name, url = await asyncio.to_thread(self.create, template)
        try:
            yield name, url
        finally:
            await asyncio.to_thread(self.remove, name)


def migrate(database_url: str) -> None:
    """Run existing migrations in a child with a private database setting."""
    environment = {**os.environ, "RAG_ACCESS_GUARD_DATABASE_URL": database_url}
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "apps/api/alembic.ini", "upgrade", "head"],
        cwd=root,
        env=environment,
        capture_output=True,
        check=False,
        timeout=120,
    )
    if result.returncode:
        message = "Disposable experiment migration failed"
        raise RuntimeError(message)
