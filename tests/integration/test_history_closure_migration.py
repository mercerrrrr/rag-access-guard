import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.integration.test_chat_history import ask
from tests.integration.test_chat_history_boundaries import add_document
from tests.support.chat import seed_pending
from tests.support.chat_generation import ChatCase


def test_closure_upgrade_preserves_trusted_legacy_sources_and_refuses_witness_loss(
    chat_case: ChatCase,
) -> None:
    _ = add_document(chat_case)
    config = Config("apps/api/alembic.ini")
    command.downgrade(config, "0010_source_order")
    with chat_case.database.begin() as connection:
        turn = seed_pending(connection, chat_case.thread)
        _ = connection.execute(
            text("""UPDATE chat_turns SET state='available',answer='LEGACY',
            completed_at=clock_timestamp(),ordinal=1,provenance_complete=true""")
        )
        _ = connection.execute(
            text("""INSERT INTO turn_sources
            (turn_id,document_id,document_version_id,chunk_id)
            SELECT :turn,document_id,document_version_id,id FROM document_chunks"""),
            {"turn": turn},
        )
        before = connection.execute(text("SELECT * FROM turn_sources ORDER BY document_id")).all()
    command.upgrade(config, "head")
    command.check(config)
    stored = chat_case.read().turns[0]
    assert stored.state == "available"
    assert stored.answer == "LEGACY"
    assert len(stored.sources) == 2
    with chat_case.database.connect() as connection:
        assert (
            connection.execute(text("SELECT * FROM turn_sources ORDER BY document_id")).all()
            == before
        )
    with pytest.raises(DBAPIError, match="History downgrade requires no witnessed answers"):
        command.downgrade(config, "0010_source_order")
    assert chat_case.read().turns[0] == stored


@pytest.mark.parametrize("witness", [None, b"x" * 32])
def test_missing_or_corrupt_closure_witness_hides_body_and_history(
    chat_case: ChatCase,
    witness: bytes | None,
) -> None:
    ask(chat_case, 1)
    with chat_case.database.begin() as connection:
        _ = connection.execute(
            text("UPDATE chat_turns SET source_closure_sha256=:witness"), {"witness": witness}
        )
    assert chat_case.read().turns[0].state == "unavailable"
    ask(chat_case, 2)
    assert "HISTORY_QUESTION_1" not in chat_case.model.inputs[-1][1]
    assert "HISTORY_ANSWER_1" not in chat_case.model.inputs[-1][1]
