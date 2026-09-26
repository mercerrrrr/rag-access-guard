from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import Connection, Engine, event
from sqlalchemy.engine.interfaces import DBAPICursor, ExecutionContext
from tests.integration.policy_probe import SQLParameters


@contextmanager
def policy_transactions() -> Generator[list[list[str]]]:
    transactions: list[list[str]] = []
    active: dict[Connection, list[str]] = {}

    def begin(connection: Connection) -> None:
        statements: list[str] = []
        active[connection] = statements
        transactions.append(statements)

    def statement(
        connection: Connection,
        _cursor: DBAPICursor,
        sql: str,
        _parameters: SQLParameters,
        _context: ExecutionContext,
        _executemany: bool,  # noqa: FBT001 -- SQLAlchemy defines this positional event argument.
    ) -> None:
        active[connection].append(sql)

    event.listen(Engine, "begin", begin)
    event.listen(Engine, "before_cursor_execute", statement)
    try:
        yield transactions
    finally:
        event.remove(Engine, "before_cursor_execute", statement)
        event.remove(Engine, "begin", begin)
