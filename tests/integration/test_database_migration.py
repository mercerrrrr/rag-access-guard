from typing import ClassVar

import pytest
from alembic import command
from alembic.config import Config
from pydantic import BaseModel, ConfigDict
from sqlalchemy import create_engine, text


class DatabaseBaseline(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    alembic_revision: str
    has_alembic_version: bool
    pgvector_version: str | None
    postgresql_major: int


def test_initial_migration_installs_required_database_baseline(
    isolated_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    monkeypatch.setenv("RAG_ACCESS_GUARD_DATABASE_URL", isolated_database_url)
    migration_config = Config("apps/api/alembic.ini")
    engine = create_engine(isolated_database_url)

    # When
    try:
        command.upgrade(migration_config, "0001_pgvector")
        with engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        """
                    SELECT
                        to_regclass('public.alembic_version') IS NOT NULL
                            AS has_alembic_version,
                        (
                            SELECT version_num
                            FROM public.alembic_version
                        ) AS alembic_revision,
                        (
                            SELECT extversion
                            FROM pg_catalog.pg_extension
                            WHERE extname = 'vector'
                        ) AS pgvector_version,
                        current_setting('server_version_num')::integer / 10000
                            AS postgresql_major
                    """
                    )
                )
                .mappings()
                .one()
            )

        command.downgrade(migration_config, "base")
        with engine.connect() as connection:
            downgraded_pgvector_version = connection.execute(
                text(
                    """
                    SELECT extversion
                    FROM pg_catalog.pg_extension
                    WHERE extname = 'vector'
                    """
                )
            ).scalar_one_or_none()
    finally:
        engine.dispose()

    baseline = DatabaseBaseline.model_validate(row)

    # Then
    assert baseline == DatabaseBaseline(
        alembic_revision="0001_pgvector",
        has_alembic_version=True,
        pgvector_version="0.8.6",
        postgresql_major=18,
    )
    assert downgraded_pgvector_version is None
