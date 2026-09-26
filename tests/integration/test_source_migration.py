import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.support.chat import seed_pending, seed_thread


@pytest.mark.usefixtures("registered_document")
def test_source_order_migration_preserves_legacy_rows_and_rolls_back_safely(
    auth_database: Engine,
) -> None:
    config = Config("apps/api/alembic.ini")
    command.downgrade(config, "0009_chat_threads")
    with auth_database.begin() as connection:
        turn = seed_pending(connection, seed_thread(connection))
        _ = connection.execute(
            text("""UPDATE chat_turns SET state='available',answer='Legacy',
            completed_at=clock_timestamp(),ordinal=1,provenance_complete=true""")
        )
        _ = connection.execute(
            text("""INSERT INTO turn_sources
            (turn_id,document_id,document_version_id,chunk_id)
            SELECT :turn,document_id,document_version_id,id FROM document_chunks LIMIT 1"""),
            {"turn": turn},
        )
        before = connection.execute(text("SELECT * FROM turn_sources")).one()
    command.upgrade(config, "head")
    command.check(config)
    with auth_database.connect() as connection:
        after = connection.execute(text("SELECT * FROM turn_sources")).one()
        assert tuple(after[:-1]) == tuple(before)
        assert after[-1] is None
    command.downgrade(config, "0009_chat_threads")
    command.upgrade(config, "head")
    with auth_database.begin() as connection:
        _ = connection.execute(text("UPDATE turn_sources SET position=0"))
    with pytest.raises(DBAPIError, match="Source downgrade requires unpositioned sources"):
        command.downgrade(config, "0009_chat_threads")
    with auth_database.connect() as connection:
        assert connection.execute(text("SELECT position FROM turn_sources")).scalar_one() == 0
        assert connection.execute(text("SELECT answer FROM chat_turns")).scalar_one() == "Legacy"
    with pytest.raises(IntegrityError), auth_database.begin() as connection:
        _ = connection.execute(text("UPDATE turn_sources SET position=-1"))
