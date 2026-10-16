"""Demo adapter for the shared loop, private reservation and exclusive crash recovery."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import httpx2
from sqlalchemy.exc import SQLAlchemyError

from experiments.demo_run_contracts import DemoRecord, DemoRunEnvelope, DemoRunPlan, DemoUnit
from experiments.demo_run_storage import DemoRunStore, finish_run
from experiments.execution_loop import ExecutionHooks, execute_schedule
from experiments.run_lock import exclusive_run_owner
from experiments.run_storage import atomic_write, create_run_directory
from rag_access_guard_api.demo.errors import DemoOwnershipError
from rag_access_guard_api.demo.evaluation import DemoDatasetError
from rag_access_guard_api.demo.private_write import owner_only
from rag_access_guard_api.services.errors import UnauthenticatedError

__all__ = ["DemoExecution", "DemoRunPlan", "DemoUnit"]


@dataclass(frozen=True, slots=True)
class DemoExecution:
    """No domain loop: scheduling and checkpoint order come from execute_schedule."""

    plan: DemoRunPlan
    execute: Callable[[DemoUnit, UUID], Awaitable[DemoRecord]]
    resume: bool = False
    comparable: Callable[[], bool] | None = None
    checkpoint_hook: Callable[[str], None] | None = None
    admission: Callable[[], Awaitable[None]] | None = None

    async def run(self) -> DemoRunEnvelope:
        """Reserve once; never replay an active, successful, or failed unit."""
        self._reserve()
        return await self._run_owned()

    def _reserve(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        if self.plan.output.resolve().is_relative_to(repository):
            raise DemoDatasetError(code="public_repository_output")
        if not self.plan.output.is_absolute() or len(
            {u.unit_id for u in self.plan.schedule}
        ) != len(self.plan.schedule):
            msg = "invalid_schedule_or_output"
            raise DemoDatasetError(msg)
        if not self.resume:
            _ = create_run_directory(self.plan.output, repository)
            owner_only(self.plan.output, initialize=True)
        elif not self.plan.output.is_dir():
            msg = "resume_run_absent"
            raise DemoDatasetError(msg)

    async def _run_owned(self) -> DemoRunEnvelope:
        with exclusive_run_owner(self.plan.output):
            store = DemoRunStore(self.plan)
            if self.resume:
                owner_only(self.plan.output)
                run, records = store.load()
            else:
                atomic_write(self.plan.output / "plan.json", self.plan.model_dump_json().encode())
                run = DemoRunEnvelope(
                    run_id=uuid4(),
                    status="running",
                    git_sha=self.plan.git_sha,
                    plan_sha256=self.plan.computed_hash(),
                    config_sha256=self.plan.config_sha256,
                    records_sha256=sha256(b"").hexdigest(),
                    scheduled_count=len(self.plan.schedule),
                    recorded_count=0,
                    failed_count=0,
                    not_attempted_count=len(self.plan.schedule),
                    model_digest=self.plan.model_digest,
                    profile_hash=self.plan.profile_hash,
                    renderer_identity=self.plan.renderer_identity,
                    corpus_sha256=self.plan.corpus_sha256,
                    started_at=datetime.now(UTC),
                )
                records: tuple[DemoRecord, ...] = ()
                run = store.checkpoint(run, records)

            if run.finished_at is not None and len(records) == len(self.plan.schedule):
                return run
            if self.admission is not None:
                try:
                    await self.admission()
                except (
                    DemoDatasetError,
                    DemoOwnershipError,
                    httpx2.HTTPError,
                    SQLAlchemyError,
                    UnauthenticatedError,
                ):
                    run = finish_run(run, comparable=True).model_copy(
                        update={"reason": "admission_failed"}
                    )
                    atomic_write(store.manifest_path(), run.model_dump_json(indent=2).encode())
                    return run

            def started(unit: DemoUnit) -> None:
                store.start(unit, records)

            async def execute(unit: DemoUnit) -> DemoRecord:
                row = await self.execute(unit, run.run_id)
                store.validate_record(run, unit, row)
                return row

            def checkpoint(terminal: tuple[DemoRecord, ...]) -> None:
                nonlocal run, records
                records = terminal
                run = store.checkpoint(run, records)
                if self.checkpoint_hook is not None:
                    self.checkpoint_hook("terminal_written")
                store.active_path().unlink()

            records = await execute_schedule(
                self.plan.schedule,
                ExecutionHooks(
                    execute=execute,
                    started=started,
                    checkpoint=checkpoint,
                    should_stop=lambda row: (
                        row.status == "interrupted"
                        or row.error_code
                        in {
                            "runtime_identity_drift",
                            "runtime_not_real_ready",
                            "input_drift",
                            "policy_revision_drift",
                            "allowed_version_drift",
                            "marker_mismatch",
                            "actor_session_invalid",
                        }
                    ),
                ),
                records,
            )
            run = finish_run(run, comparable=self.comparable is None or self.comparable())
            atomic_write(store.manifest_path(), run.model_dump_json(indent=2).encode())
            return run
