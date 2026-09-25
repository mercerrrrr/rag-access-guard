import pytest
from sqlalchemy import Connection, inspect, text
from sqlalchemy.exc import IntegrityError

from tests.support.chat import seed_pending, seed_thread


def test_chat_tables_are_migrated(schema_connection: Connection) -> None:
    assert {"chat_threads", "chat_turns", "turn_sources"} <= set(
        inspect(schema_connection).get_table_names()
    )


def test_thread_owner_is_immutable(seeded_connection: Connection) -> None:
    _ = seed_thread(seeded_connection)
    with (
        pytest.raises(IntegrityError, match="Thread owner is immutable"),
        seeded_connection.begin_nested(),
    ):
        _ = seeded_connection.execute(
            text("UPDATE chat_threads SET owner_user_id=gen_random_uuid()")
        )


def test_thread_owner_foreign_key_is_restrictive(seeded_connection: Connection) -> None:
    _ = seed_thread(seeded_connection)
    foreign_keys = inspect(seeded_connection).get_foreign_keys("chat_threads")
    owner_key = next(key for key in foreign_keys if key["constrained_columns"] == ["owner_user_id"])
    assert owner_key.get("options", {}).get("ondelete") == "RESTRICT"
    with pytest.raises(IntegrityError), seeded_connection.begin_nested():
        _ = seeded_connection.execute(
            text("""INSERT INTO chat_threads(id,owner_user_id)
            VALUES(gen_random_uuid(),gen_random_uuid())""")
        )


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE chat_threads SET revision=-1",
        "UPDATE chat_turns SET state='unavailable'",
        "UPDATE chat_turns SET state='failed'",
        "UPDATE chat_turns SET request_sha256=decode('ab','hex')",
        "UPDATE chat_turns SET expected_thread_revision=-1",
        "UPDATE chat_turns SET answer='unreleased'",
        "UPDATE chat_turns SET ordinal=1",
        "UPDATE chat_turns SET completed_at=clock_timestamp()",
        "UPDATE chat_turns SET provenance_complete=true",
        "UPDATE chat_turns SET server_generated_neutral=true",
        "UPDATE chat_turns SET neutral_reason='no_context'",
        "UPDATE chat_turns SET lease_expires_at=NULL",
        """UPDATE chat_turns SET state='available',answer='body',
        completed_at=clock_timestamp(),ordinal=1""",
        """UPDATE chat_turns SET state='neutral',server_generated_neutral=true,
        completed_at=clock_timestamp(),ordinal=1""",
        """UPDATE chat_turns SET state='neutral',server_generated_neutral=true,
        completed_at=clock_timestamp(),ordinal=1,neutral_reason='raw exception'""",
    ],
)
def test_invalid_chat_states_are_rejected(change: str, seeded_connection: Connection) -> None:
    _ = seed_pending(seeded_connection, seed_thread(seeded_connection))
    with pytest.raises(IntegrityError), seeded_connection.begin_nested():
        _ = seeded_connection.execute(text(change))


def test_only_one_pending_turn_per_thread(seeded_connection: Connection) -> None:
    thread = seed_thread(seeded_connection)
    _ = seed_pending(seeded_connection, thread)
    with (
        pytest.raises(IntegrityError, match="uq_chat_turns_pending"),
        seeded_connection.begin_nested(),
    ):
        _ = seed_pending(seeded_connection, thread)


def test_completed_request_identity_cannot_be_reused(seeded_connection: Connection) -> None:
    thread = seed_thread(seeded_connection)
    _ = seed_pending(seeded_connection, thread)
    _ = seeded_connection.execute(
        text("""UPDATE chat_turns SET state='neutral',
        completed_at=clock_timestamp(),ordinal=1,server_generated_neutral=true,neutral_reason='no_context'""")
    )
    with (
        pytest.raises(IntegrityError, match="uq_chat_turns_request"),
        seeded_connection.begin_nested(),
    ):
        _ = seeded_connection.execute(
            text("""INSERT INTO chat_turns
            (id,thread_id,request_id,request_sha256,expected_thread_revision,user_input,lease_expires_at)
            SELECT gen_random_uuid(),thread_id,request_id,request_sha256,1,'different question',
            clock_timestamp()+interval '180 seconds' FROM chat_turns""")
        )


def test_completed_ordinal_is_unique(seeded_connection: Connection) -> None:
    thread = seed_thread(seeded_connection)
    _ = seed_pending(seeded_connection, thread)
    _ = seeded_connection.execute(
        text("""UPDATE chat_turns SET state='neutral',
        completed_at=clock_timestamp(),ordinal=1,server_generated_neutral=true,neutral_reason='interrupted'""")
    )
    with (
        pytest.raises(IntegrityError, match="uq_chat_turns_ordinal"),
        seeded_connection.begin_nested(),
    ):
        _ = seeded_connection.execute(
            text("""INSERT INTO chat_turns
            (id,thread_id,request_id,request_sha256,expected_thread_revision,user_input,state,
            ordinal,completed_at,server_generated_neutral,neutral_reason)
            SELECT gen_random_uuid(),thread_id,gen_random_uuid(),request_sha256,1,'question',
            'neutral',1,clock_timestamp(),true,'no_context' FROM chat_turns""")
        )


def test_turn_cannot_reference_missing_thread(seeded_connection: Connection) -> None:
    with pytest.raises(IntegrityError), seeded_connection.begin_nested():
        _ = seeded_connection.execute(
            text("""INSERT INTO chat_turns
            (id,thread_id,request_id,request_sha256,expected_thread_revision,user_input,lease_expires_at)
            VALUES(gen_random_uuid(),gen_random_uuid(),gen_random_uuid(),decode(repeat('ab',32),'hex'),
            0,'question',clock_timestamp()+interval '180 seconds')""")
        )
