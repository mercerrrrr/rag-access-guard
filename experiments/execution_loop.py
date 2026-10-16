"""The single domain-neutral scheduled execution and checkpoint loop."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExecutionHooks[Unit, Record]:
    """Adapters own admission, records, durable start markers and schema writers."""

    execute: Callable[[Unit], Awaitable[Record]]
    started: Callable[[Unit], None]
    checkpoint: Callable[[tuple[Record, ...]], None]
    should_stop: Callable[[Record], bool]


async def execute_schedule[Unit, Record](
    schedule: tuple[Unit, ...],
    hooks: ExecutionHooks[Unit, Record],
    initial: tuple[Record, ...] = (),
) -> tuple[Record, ...]:
    """Persist admission before a unit and its terminal row before advancing."""
    records = list(initial)
    for unit in schedule[len(records) :]:
        hooks.started(unit)
        record = await hooks.execute(unit)
        records.append(record)
        hooks.checkpoint(tuple(records))
        if hooks.should_stop(record):
            break
    return tuple(records)
