"""Observation-only probes for real PostgreSQL transaction ordering."""

from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime
from time import monotonic
from uuid import UUID

from sqlalchemy import Connection, Engine, event, text
from sqlalchemy.engine.interfaces import DBAPICursor, ExecutionContext

type SQLValue = str | int | float | bytes | datetime | UUID | None
type SQLParameters = Mapping[str, SQLValue] | Sequence[SQLValue] | Sequence[Mapping[str, SQLValue]]


@contextmanager
def capture_sql() -> Generator[list[str]]:
    """Capture statements, never bound credentials or payloads."""
    statements: list[str] = []

    def observe(
        _connection: Connection,
        _cursor: DBAPICursor,
        statement: str,
        _parameters: SQLParameters,
        _context: ExecutionContext,
        _executemany: bool,  # noqa: FBT001 - SQLAlchemy requires this positional event argument.
    ) -> None:
        statements.append(statement)

    event.listen(Engine, "before_cursor_execute", observe)
    try:
        yield statements
    finally:
        event.remove(Engine, "before_cursor_execute", observe)


def wait_for_policy_wait(engine: Engine, blocker: int) -> None:
    """Require an observed policy lock wait, not an elapsed delay."""
    deadline = monotonic() + 10
    with engine.connect() as observer:
        while True:
            if observer.execute(
                text("""SELECT EXISTS (
                SELECT 1 FROM pg_stat_activity WHERE datname=current_database()
                AND :blocker=ANY(pg_blocking_pids(pid)) AND wait_event_type='Lock'
                AND query LIKE '%policy_state%' AND query LIKE '%FOR %')"""),
                {"blocker": blocker},
            ).scalar_one():
                return
            assert monotonic() < deadline, "Expected policy lock wait was not observed"
            observer.rollback()
