from collections.abc import Mapping, Sequence
from types import TracebackType
from typing import Self

from pydantic import JsonValue
from sqlalchemy import Connection, Engine, event
from sqlalchemy.engine import URL
from sqlalchemy.sql import ClauseElement


class RecordingWrites:
    """Mutable SQL observer scoped to one disposable target and its preparation call."""

    def __init__(self, target: URL) -> None:
        self.target: URL = target
        self.writes: list[str] = []

    @property
    def write_count(self) -> int:
        return len(self.writes)

    def record(
        self,
        connection: Connection,
        statement: ClauseElement,
        _multiparams: Sequence[Mapping[str, JsonValue]],
        _params: Mapping[str, JsonValue],
        _options: Mapping[str, JsonValue],
    ) -> None:
        """Follow SQLAlchemy's event signature without changing the real SQL execution."""
        if connection.engine.url == self.target:
            operation = str(statement).lstrip().split(maxsplit=1)[0].upper()
            if operation in {"CREATE", "ALTER", "DROP", "INSERT", "UPDATE", "DELETE", "TRUNCATE"}:
                self.writes.append(operation)

    def __enter__(self) -> Self:
        event.listen(Engine, "before_execute", self.record)
        return self

    def __exit__(
        self,
        _kind: type[BaseException] | None,
        _error: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        event.remove(Engine, "before_execute", self.record)
