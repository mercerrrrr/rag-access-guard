import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from tests.support.chat import seed_pending, seed_thread
from tests.support.chat_generation import ChatCase


def test_help_upgrade_preserves_existing_rows_and_safe_downgrade(auth_database: Engine) -> None:
    config = Config("apps/api/alembic.ini")
    command.downgrade(config, "0014_demo_seed_state")
    with auth_database.begin() as connection:
        _ = seed_pending(connection, seed_thread(connection))
        before = connection.execute(text("SELECT * FROM chat_turns")).one()
        users = connection.execute(text("SELECT id,login,password_hash FROM users")).all()
    command.upgrade(config, "head")
    command.check(config)
    with auth_database.connect() as connection:
        assert connection.execute(text("SELECT * FROM chat_turns")).one() == before
        assert connection.execute(text("SELECT id,login,password_hash FROM users")).all() == users
    command.downgrade(config, "0014_demo_seed_state")
    command.upgrade(config, "head")
    with auth_database.connect() as connection:
        assert connection.execute(text("SELECT * FROM chat_turns")).one() == before


@pytest.mark.parametrize("row", ["turn", "audit"])
def test_help_downgrade_refuses_history_or_audit_loss(chat_case: ChatCase, row: str) -> None:
    with chat_case.database.begin() as connection:
        if row == "turn":
            _ = seed_pending(connection, chat_case.thread)
            _ = connection.execute(
                text("""UPDATE chat_turns SET state='neutral',
                neutral_reason='assistant_help',server_generated_neutral=true,
                completed_at=clock_timestamp(),ordinal=1,lease_expires_at=NULL""")
            )
        else:
            _ = connection.execute(
                text("""INSERT INTO audit_events
                (id,event_type,stage,outcome,policy_revision,source_count)
                VALUES(gen_random_uuid(),'assistant_help','release','success',0,0)""")
            )
    with pytest.raises(DBAPIError, match="help downgrade would discard supported history"):
        command.downgrade(Config("apps/api/alembic.ini"), "0014_demo_seed_state")
    with chat_case.database.connect() as connection:
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "0015_assistant_help"
        )
