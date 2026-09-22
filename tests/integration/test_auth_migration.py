import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


def test_auth_migration_roundtrip(
    isolated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAG_ACCESS_GUARD_DATABASE_URL", isolated_database_url)
    config = Config("apps/api/alembic.ini")
    command.upgrade(config, "head")
    command.check(config)
    engine = create_engine(isolated_database_url)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "0003_auth_challenges"
            )
        command.downgrade(config, "0002_access_control")
        with engine.connect() as connection:
            assert "auth_challenges" not in inspect(connection).get_table_names()
            assert "sessions" in inspect(connection).get_table_names()
        command.upgrade(config, "head")
        command.check(config)
    finally:
        engine.dispose()


@pytest.mark.parametrize("violation", ["short-token", "short-csrf", "expiry", "consumed"])
def test_challenge_constraints(
    isolated_database_url: str, monkeypatch: pytest.MonkeyPatch, violation: str
) -> None:
    monkeypatch.setenv("RAG_ACCESS_GUARD_DATABASE_URL", isolated_database_url)
    command.upgrade(Config("apps/api/alembic.ini"), "head")
    engine = create_engine(isolated_database_url)
    try:
        with pytest.raises(IntegrityError), engine.begin() as connection:
            _ = connection.execute(
                text("""INSERT INTO auth_challenges
                (id, token_digest, csrf_token_digest, created_at, expires_at, consumed_at)
                VALUES (gen_random_uuid(), :token, :csrf, now(),
                        now() + make_interval(mins => :minutes),
                        now() + make_interval(mins => :consumed))"""),
                {
                    "token": b"t" * (31 if violation == "short-token" else 32),
                    "csrf": b"c" * (31 if violation == "short-csrf" else 32),
                    "minutes": 9 if violation == "expiry" else 10,
                    "consumed": -1 if violation == "consumed" else 0,
                },
            )
    finally:
        engine.dispose()
