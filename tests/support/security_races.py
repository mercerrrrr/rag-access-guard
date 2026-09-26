from collections.abc import Iterator
from dataclasses import dataclass, field
from threading import Event
from time import monotonic
from uuid import UUID

import pytest
from anyio.to_thread import run_sync
from pydantic import TypeAdapter
from sqlalchemy import Integer, String, column, func, select, text
from sqlalchemy.ext.asyncio import AsyncConnection
from tests.support.chat_generation import ChatCase

from rag_access_guard import Guard, PolicyReader, PreparedContext, ReleaseDecision
from rag_access_guard_api.services import security
from rag_access_guard_api.services.audit import AuditRecord
from rag_access_guard_api.services.security import MutationUoW


@dataclass(slots=True)
class RaceHarness:
    case: ChatCase
    gate_locked: Event = field(default_factory=Event)
    mutation_locked: Event = field(default_factory=Event)
    continue_gate: Event = field(default_factory=Event)
    continue_mutation: Event = field(default_factory=Event)
    hold_gate: bool = False
    hold_mutation: bool = False
    gate_pid: int | None = None
    mutation_pid: int | None = None
    waits: list[tuple[int, int]] = field(default_factory=list)
    isolation: list[str] = field(default_factory=list)
    releases: list[ReleaseDecision] = field(default_factory=list)

    def wait_for_blocked(self, blocker_pid: int) -> int:
        deadline = monotonic() + 10
        with self.case.database.connect() as observer:
            observer_pid = observer.execute(select(func.pg_backend_pid(type_=Integer))).scalar_one()
            while True:
                blocked = TypeAdapter[int | None](int | None).validate_python(
                    observer.execute(
                        text("""SELECT pid FROM pg_stat_activity
                    WHERE datname=current_database() AND :blocker=ANY(pg_blocking_pids(pid))
                    AND wait_event_type='Lock' AND query LIKE '%policy_state%'
                    AND query LIKE '%FOR %'""").columns(column("pid", Integer)),
                        {"blocker": blocker_pid},
                    ).scalar_one_or_none()
                )
                if blocked is not None:
                    assert len({blocked, blocker_pid, observer_pid}) == 3
                    self.waits.append((blocked, blocker_pid))
                    return blocked
                assert monotonic() < deadline, "Expected exact PostgreSQL policy wait was absent"
                observer.rollback()

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        original_lock = security.lock_policy
        original_mutation = MutationUoW.record_change
        original_release = Guard.authorize_release

        async def lock(connection: AsyncConnection, *, exclusive: bool) -> int:
            revision = await original_lock(connection, exclusive=exclusive)
            self.isolation.append(
                (
                    await connection.execute(
                        select(func.current_setting("transaction_isolation", type_=String))
                    )
                ).scalar_one()
            )
            if not exclusive and self.hold_gate and not self.gate_locked.is_set():
                self.gate_pid = (
                    await connection.execute(select(func.pg_backend_pid(type_=Integer)))
                ).scalar_one()
                self.gate_locked.set()
                assert await run_sync(self.continue_gate.wait, 15)
            return revision

        async def mutation(uow: MutationUoW, event: AuditRecord) -> int:
            revision = await original_mutation(uow, event)
            if self.hold_mutation and not self.mutation_locked.is_set():
                self.mutation_pid = (
                    await uow.connection.execute(select(func.pg_backend_pid(type_=Integer)))
                ).scalar_one()
                self.mutation_locked.set()
                assert await run_sync(self.continue_mutation.wait, 15)
            return revision

        async def release(
            guard: Guard,
            principal_id: UUID,
            thread_id: UUID,
            prepared: PreparedContext,
            policy_reader: PolicyReader,
        ) -> ReleaseDecision:
            result = await original_release(guard, principal_id, thread_id, prepared, policy_reader)
            self.releases.append(result)
            return result

        monkeypatch.setattr(security, "lock_policy", lock)
        monkeypatch.setattr(MutationUoW, "record_change", mutation)
        monkeypatch.setattr(Guard, "authorize_release", release)


@pytest.fixture
def race_case(chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch) -> Iterator[RaceHarness]:
    harness = RaceHarness(chat_case)
    harness.install(monkeypatch)
    try:
        yield harness
    finally:
        harness.continue_gate.set()
        harness.continue_mutation.set()
        chat_case.model.resume.set()
