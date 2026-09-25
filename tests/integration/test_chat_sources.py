from uuid import UUID

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import IntegrityError

from tests.support.chat import seed_pending, seed_thread

pytestmark = pytest.mark.usefixtures("registered_document")


def _attach_source(connection: Connection, turn: UUID) -> None:
    _ = connection.execute(
        text("""INSERT INTO turn_sources
        (turn_id,document_id,document_version_id,chunk_id)
        SELECT :turn,document_id,document_version_id,id FROM document_chunks LIMIT 1"""),
        {"turn": turn},
    )


def _answer(connection: Connection) -> None:
    _ = connection.execute(
        text("""UPDATE chat_turns SET state='available',answer='Synthetic answer',
        completed_at=clock_timestamp(),ordinal=1,provenance_complete=true""")
    )


def test_available_turn_requires_canonical_sources(auth_database: Engine) -> None:
    with auth_database.begin() as connection:
        _ = seed_pending(connection, seed_thread(connection))
        with connection.begin_nested() as savepoint:
            _answer(connection)
            with pytest.raises(IntegrityError, match="Invalid turn sources"):
                _ = connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            savepoint.rollback()


@pytest.mark.parametrize("state", ["pending", "neutral"])
def test_non_answer_turn_cannot_keep_sources(
    state: str,
    auth_database: Engine,
) -> None:
    with auth_database.connect() as connection:
        turn = seed_pending(connection, seed_thread(connection))
        _attach_source(connection, turn)
        if state == "neutral":
            _ = connection.execute(
                text("""UPDATE chat_turns SET state='neutral',
                completed_at=clock_timestamp(),ordinal=1,server_generated_neutral=true,
                neutral_reason='no_context'""")
            )
        with pytest.raises(IntegrityError, match="Invalid turn sources"):
            connection.commit()


def test_answer_and_sources_commit_together(
    auth_database: Engine,
) -> None:
    with auth_database.begin() as connection:
        turn = seed_pending(connection, seed_thread(connection))
        _attach_source(connection, turn)
        _answer(connection)
    with auth_database.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM turn_sources")).scalar_one() == 1
        assert connection.execute(text("SELECT state FROM chat_turns")).scalar_one() == "available"


def test_deleting_last_source_cannot_leave_available_answer(
    auth_database: Engine,
) -> None:
    with auth_database.begin() as connection:
        _attach_source(connection, seed_pending(connection, seed_thread(connection)))
        _answer(connection)
    with (
        pytest.raises(IntegrityError, match="Invalid turn sources"),
        auth_database.begin() as connection,
    ):
        _ = connection.execute(text("DELETE FROM turn_sources"))


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE turn_sources SET document_id=gen_random_uuid()",
        "UPDATE turn_sources SET document_version_id=gen_random_uuid()",
        "UPDATE turn_sources SET chunk_id=gen_random_uuid()",
        "UPDATE turn_sources SET turn_id=gen_random_uuid()",
        "INSERT INTO turn_sources SELECT * FROM turn_sources",
        "DELETE FROM chat_turns",
        "DELETE FROM chat_threads",
    ],
)
def test_source_witness_and_restrictive_links_are_enforced(
    statement: str,
    auth_database: Engine,
) -> None:
    with auth_database.begin() as connection:
        _attach_source(connection, seed_pending(connection, seed_thread(connection)))
        _answer(connection)
    with pytest.raises(IntegrityError), auth_database.begin() as connection:
        _ = connection.execute(text(statement))


def test_existing_but_mixed_document_witness_is_rejected(
    auth_database: Engine,
) -> None:
    with auth_database.begin() as connection:
        _attach_source(connection, seed_pending(connection, seed_thread(connection)))
        _answer(connection)
        _ = connection.execute(
            text("""INSERT INTO documents(id,title,created_by)
            SELECT gen_random_uuid(),'Other',id FROM users WHERE login='reader'""")
        )
    with (
        pytest.raises(IntegrityError, match="fk_turn_sources_chunk"),
        auth_database.begin() as connection,
    ):
        _ = connection.execute(
            text("""UPDATE turn_sources SET document_id=(
            SELECT id FROM documents WHERE title='Other')""")
        )
