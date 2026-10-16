"""Atomic private run lifecycle around the isolated paired experiment harness."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal

from experiments.comparison_config import ComparisonConfig
from experiments.execution_loop import ExecutionHooks, execute_schedule
from experiments.harness import ScenarioHarness
from experiments.run_manifest import RunCounts, RunManifest, TrialSpec
from experiments.run_storage import atomic_write
from experiments.scenario_types import Scenario
from experiments.trials import TrialRecord, execute_trial


@dataclass(frozen=True, slots=True)
class RunExecution:
    """Persist each terminal trial before moving to the next scheduled pair."""

    manifest: RunManifest
    config: ComparisonConfig
    scenarios: dict[str, Scenario]
    harness: ScenarioHarness
    output: Path
    verify_inputs: Callable[[], bool] | None = None

    def _checkpoint(
        self,
        records: tuple[TrialRecord, ...],
        *,
        status: Literal["running", "completed", "failed", "invalid"],
        reason: str | None,
    ) -> RunManifest:
        raw = b"".join((record.model_dump_json() + "\n").encode() for record in records)
        atomic_write(self.output / "records.jsonl", raw)
        counts = RunCounts(
            scheduled=len(self.manifest.schedule),
            completed=sum(record.status == "completed" for record in records),
            failed=sum(record.status == "failed" for record in records),
            invalid=sum(record.status == "invalid" for record in records),
            attempts=sum(len(arm.result.attempts) for record in records for arm in record.arms),
        )
        manifest = self.manifest.model_copy(
            update={
                "status": status,
                "reason": reason,
                "counts": counts,
                "finished_at": None if status == "running" else datetime.now(UTC),
                "records_sha256": sha256(raw).hexdigest(),
                "live_status": (
                    "not_run"
                    if self.manifest.live_status == "not_run"
                    else status
                    if status in {"running", "completed"}
                    else "failed"
                ),
                "live_reason": self.manifest.live_reason
                if self.manifest.live_status == "not_run"
                else reason,
            }
        )
        atomic_write(self.output / "manifest.json", manifest.model_dump_json(indent=2).encode())
        return manifest

    async def run(self) -> RunManifest:
        """Return the actual terminal run state, never an inferred success."""
        records: tuple[TrialRecord, ...] = ()
        _ = self._checkpoint(records, status="running", reason=None)
        status: Literal["completed", "failed", "invalid"] = "failed"
        reason: str | None = "run_interrupted"
        try:

            async def execute(trial: TrialSpec) -> TrialRecord:
                return await execute_trial(
                    trial, self.scenarios[trial.case_id], self.config, self.harness
                )

            def checkpoint(terminal: tuple[TrialRecord, ...]) -> None:
                nonlocal records
                records = terminal
                _ = self._checkpoint(records, status="running", reason=None)

            records = await execute_schedule(
                self.manifest.schedule,
                ExecutionHooks(
                    execute=execute,
                    started=lambda _trial: None,
                    checkpoint=checkpoint,
                    should_stop=lambda record: record.error_code == "interrupted",
                ),
            )
            if len(records) != len(self.manifest.schedule) or any(
                record.status == "failed" for record in records
            ):
                status, reason = "failed", "failed_or_incomplete_trials"
            elif self.verify_inputs is not None and not self.verify_inputs():
                status, reason = "invalid", "input_drift"
            elif self.manifest.dirty:
                status, reason = "invalid", "dirty_code_diagnostic_only"
            elif any(record.status == "invalid" for record in records):
                status, reason = "invalid", "invalid_trials"
            elif any(
                arm.result.config_hash
                != self.manifest.identity.trial_config_hashes.get(record.trial.trial_id)
                or arm.result.corpus_hash != self.manifest.identity.corpus_hash
                or arm.result.model_identity != self.config.model_identity
                or arm.result.tokenizer_identity != self.config.tokenizer_identity
                or arm.result.renderer_identity != self.config.renderer_identity
                for record in records
                for arm in record.arms
            ):
                status, reason = "invalid", "identity_mismatch"
            else:
                status, reason = "completed", None
        finally:
            manifest = self._checkpoint(records, status=status, reason=reason)
        return manifest
