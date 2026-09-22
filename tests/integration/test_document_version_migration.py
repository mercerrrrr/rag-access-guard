import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def test_registry_migration_roundtrip_preserves_legacy_documents(
    isolated_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a logical document created before version storage existed.
    monkeypatch.setenv("RAG_ACCESS_GUARD_DATABASE_URL", isolated_database_url)
    config = Config("apps/api/alembic.ini")
    command.upgrade(config, "0003_auth_challenges")
    engine = create_engine(isolated_database_url)
    try:
        with engine.begin() as connection:
            _ = connection.execute(
                text("""INSERT INTO users (id, login, display_name, password_hash)
                VALUES (gen_random_uuid(), 'legacy', 'Legacy', 'synthetic')""")
            )
            _ = connection.execute(
                text("""INSERT INTO documents (id, title, created_by)
                SELECT gen_random_uuid(), 'Legacy', id FROM users""")
            )
        # When: upgrading, rolling back the new schema, then upgrading again.
        command.upgrade(config, "head")
        command.check(config)
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT active_version_id FROM documents")).scalar_one()
                is None
            )
            assert "document_versions" in inspect(connection).get_table_names()
        command.downgrade(config, "0003_auth_challenges")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT title FROM documents")).scalar_one() == "Legacy"
            assert "document_versions" not in inspect(connection).get_table_names()
        command.upgrade(config, "head")
        command.check(config)
        # Then: legacy identity remains intact without invented provenance.
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT title, active_version_id FROM documents")
            ).one() == ("Legacy", None)
    finally:
        engine.dispose()
