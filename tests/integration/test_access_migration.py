import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def test_access_migration_roundtrip_preserves_vector(
    isolated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAG_ACCESS_GUARD_DATABASE_URL", isolated_database_url)
    config = Config("apps/api/alembic.ini")
    engine = create_engine(isolated_database_url)
    try:
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == ("0009_chat_threads")
            assert connection.execute(text("SELECT revision FROM policy_state")).scalar_one() == 0
        command.check(config)
        command.downgrade(config, "0001_pgvector")
        with engine.connect() as connection:
            assert inspect(connection).get_table_names() == ["alembic_version"]
            assert (
                connection.execute(
                    text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
                ).scalar_one()
                == "0.8.6"
            )
        command.upgrade(config, "head")
        command.check(config)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT id, revision FROM policy_state")).one() == (1, 0)
    finally:
        engine.dispose()
