"""Database engine construction and readiness checks."""

from typing import Final

import anyio
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.sql.elements import TextClause

from rag_access_guard_api.config import (
    DATABASE_MAX_OVERFLOW,
    DATABASE_POOL_SIZE,
    DATABASE_TIMEOUT_SECONDS,
    Settings,
)

EXPECTED_ALEMBIC_REVISION: Final = "0008_pdf_ingestion"
EXPECTED_PGVECTOR_VERSION: Final = "0.8.6"
EXPECTED_POSTGRESQL_MAJOR: Final = 18

_READINESS_QUERY: Final[TextClause] = text(
    """
    SELECT 1
    FROM pg_catalog.pg_extension AS installed_extension
    JOIN public.alembic_version AS migration
      ON migration.version_num = :revision
    WHERE installed_extension.extname = 'vector'
      AND installed_extension.extversion = :vector_version
      AND current_setting('server_version_num')::integer / 10000 = :postgres_major
    """
)


def create_database_engine(settings: Settings) -> AsyncEngine:
    """Create the shared asynchronous database engine."""
    return create_async_engine(
        str(settings.database_url),
        hide_parameters=True,
        connect_args={"connect_timeout": DATABASE_TIMEOUT_SECONDS},
        max_overflow=DATABASE_MAX_OVERFLOW,
        pool_pre_ping=True,
        pool_size=DATABASE_POOL_SIZE,
        pool_timeout=DATABASE_TIMEOUT_SECONDS,
    )


async def is_database_ready(engine: AsyncEngine) -> bool:
    """Check the required PostgreSQL, pgvector and migration baseline."""
    try:
        with anyio.fail_after(DATABASE_TIMEOUT_SECONDS):
            async with engine.connect() as connection:
                result = await connection.execute(
                    _READINESS_QUERY,
                    {
                        "postgres_major": EXPECTED_POSTGRESQL_MAJOR,
                        "revision": EXPECTED_ALEMBIC_REVISION,
                        "vector_version": EXPECTED_PGVECTOR_VERSION,
                    },
                )
                _ = result.one()
    except (SQLAlchemyError, TimeoutError):
        return False

    return True
