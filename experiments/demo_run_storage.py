"""Typed schema-two records and recovery of the two atomic checkpoint windows."""

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from experiments.demo_run_contracts import (
    RECORD_ADAPTER,
    ActiveUnit,
    DemoRecord,
    DemoRunEnvelope,
    DemoRunPlan,
    DemoUnit,
)
from experiments.run_storage import atomic_write
from rag_access_guard_api.demo.calibration_contracts import DemoCalibrationResult
from rag_access_guard_api.demo.evaluation import DemoDatasetError
from rag_access_guard_api.demo.evaluation_contracts import DemoCaseResult


def record_bytes(records: tuple[DemoRecord, ...]) -> bytes:
    """Terminal JSONL has one canonical typed row per immutable scheduled unit."""
    return b"".join((record.model_dump_json() + "\n").encode() for record in records)


def interrupted_result(plan: DemoRunPlan, run: DemoRunEnvelope, unit: DemoUnit) -> DemoRecord:
    """An active unit has unknown output and duration and is never submitted again."""
    if plan.mode == "calibrate":
        return DemoCalibrationResult(
            run_id=run.run_id,
            unit_id=unit.unit_id,
            case_id=unit.case_id,
            principal_login=unit.gold.principal_login,
            status="interrupted",
            elapsed_ms=None,
            scores=None,
            relevant=(),
            model_digest=None,
            profile_hash=plan.profile_hash,
            config_hash=plan.retrieval_config_hash,
            policy_revision=None,
            error_code="interrupted",
            gold=unit.gold,
            dataset_sha256=plan.dataset_sha256,
        )
    return DemoCaseResult(
        run_id=run.run_id,
        unit_id=unit.unit_id,
        case_id=unit.case_id,
        round_number=unit.round_number,
        warmup=unit.warmup,
        principal_login=unit.gold.principal_login,
        request_id=unit.request_id,
        status="interrupted",
        elapsed_ms=None,
        phase_timings=None,
        retrieved_refs=None,
        released_refs=None,
        answer=None,
        model_digest=None,
        profile_hash=plan.profile_hash,
        config_hash=plan.retrieval_config_hash,
        policy_revision=None,
        error_code="interrupted",
        telemetry_reason="process_interrupted_unknown_outcome",
        gold=unit.gold,
        dataset_sha256=plan.dataset_sha256,
    )


@dataclass(frozen=True, slots=True)
class DemoRunStore:
    """The adapter owns serializers; the common runner only sees callbacks."""

    plan: DemoRunPlan

    def manifest_path(self) -> Path:
        """Return the single schema-two envelope location."""
        return self.plan.output / "manifest.json"

    def active_path(self) -> Path:
        """Return the durable in-flight unit marker location."""
        return self.plan.output / "active.json"

    def start(self, unit: DemoUnit, records: tuple[DemoRecord, ...]) -> None:
        """Publish the may-have-started marker atomically before the callback."""
        atomic_write(
            self.active_path(),
            ActiveUnit(
                unit_id=unit.unit_id,
                preceding_records_sha256=sha256(record_bytes(records)).hexdigest(),
            )
            .model_dump_json()
            .encode(),
        )

    def checkpoint(self, run: DemoRunEnvelope, records: tuple[DemoRecord, ...]) -> DemoRunEnvelope:
        """Persist terminal rows before the envelope and active-marker clearing."""
        raw = record_bytes(records)
        failed = sum(row.status != "success" for row in records)
        updated = run.model_copy(
            update={
                "recorded_count": len(records),
                "failed_count": failed,
                "not_attempted_count": len(self.plan.schedule) - len(records),
                "records_sha256": sha256(raw).hexdigest(),
            }
        )
        atomic_write(self.plan.output / "records.jsonl", raw)
        atomic_write(self.manifest_path(), updated.model_dump_json(indent=2).encode())
        return updated

    def load(self) -> tuple[DemoRunEnvelope, tuple[DemoRecord, ...]]:
        """Reject drift and reconcile only a uniquely proven checkpoint crash window."""
        saved = DemoRunPlan.model_validate_json((self.plan.output / "plan.json").read_bytes())
        run = DemoRunEnvelope.model_validate_json(self.manifest_path().read_bytes())
        if (
            saved != self.plan
            or run.plan_sha256 != self.plan.computed_hash()
            or run.git_sha != self.plan.git_sha
            or run.config_sha256 != self.plan.config_sha256
            or run.profile_hash != self.plan.profile_hash
            or run.model_digest != self.plan.model_digest
            or run.renderer_identity != self.plan.renderer_identity
            or run.corpus_sha256 != self.plan.corpus_sha256
        ):
            msg = "resume_identity_drift"
            raise DemoDatasetError(msg)
        raw = (self.plan.output / "records.jsonl").read_bytes()
        records = tuple(RECORD_ADAPTER.validate_json(line) for line in raw.splitlines())
        if raw != record_bytes(records) or len(records) > len(self.plan.schedule):
            msg = "malformed_records"
            raise DemoDatasetError(msg)
        for unit, row in zip(self.plan.schedule, records, strict=False):
            self.validate_record(run, unit, row)
        if len({row.unit_id for row in records}) != len(records):
            msg = "duplicate_record"
            raise DemoDatasetError(msg)
        active = (
            ActiveUnit.model_validate_json(self.active_path().read_bytes())
            if self.active_path().exists()
            else None
        )
        if sha256(raw).hexdigest() != run.records_sha256:
            if (
                active is None
                or not records
                or len(records) != run.recorded_count + 1
                or records[-1].unit_id != active.unit_id
                or sha256(record_bytes(records[:-1])).hexdigest() != run.records_sha256
                or active.preceding_records_sha256 != run.records_sha256
            ):
                msg = "records_hash_mismatch"
                raise DemoDatasetError(msg)
            run = self.checkpoint(run, records)
        if (
            run.recorded_count != len(records)
            or run.scheduled_count != len(self.plan.schedule)
            or run.failed_count != sum(r.status != "success" for r in records)
            or run.not_attempted_count != len(self.plan.schedule) - len(records)
        ):
            msg = "record_counts_mismatch"
            raise DemoDatasetError(msg)
        return self._recover_active(run, records, active)

    def _recover_active(
        self, run: DemoRunEnvelope, records: tuple[DemoRecord, ...], active: ActiveUnit | None
    ) -> tuple[DemoRunEnvelope, tuple[DemoRecord, ...]]:
        if active is not None:
            index = next(
                (n for n, unit in enumerate(self.plan.schedule) if unit.unit_id == active.unit_id),
                -1,
            )
            if index == len(records):
                if active.preceding_records_sha256 != run.records_sha256:
                    msg = "active_record_drift"
                    raise DemoDatasetError(msg)
                records = (*records, interrupted_result(self.plan, run, self.plan.schedule[index]))
                run = self.checkpoint(run, records)
            elif index != len(records) - 1 or index < 0:
                msg = "active_schedule_drift"
                raise DemoDatasetError(msg)
            self.active_path().unlink()
        return run, records

    def validate_record(self, run: DemoRunEnvelope, unit: DemoUnit, row: DemoRecord) -> None:
        """Reject callback or on-disk rows that claim another actor, gold, or identity."""
        if (
            row.run_id != run.run_id
            or row.unit_id != unit.unit_id
            or row.case_id != unit.case_id
            or row.gold != unit.gold
            or row.principal_login != unit.gold.principal_login
            or row.request_id != unit.request_id
            or row.round_number != unit.round_number
            or row.warmup != unit.warmup
            or row.dataset_sha256 != self.plan.dataset_sha256
            or row.profile_hash != self.plan.profile_hash
            or row.config_hash != self.plan.retrieval_config_hash
            or row.model_digest not in {None, self.plan.model_digest}
        ):
            msg = "record_identity_drift"
            raise DemoDatasetError(msg)


def finish_run(run: DemoRunEnvelope, *, comparable: bool) -> DemoRunEnvelope:
    """A complete recording is distinct from passing human factual evaluation."""
    status = (
        "invalid"
        if not comparable
        else ("failed" if run.failed_count or run.not_attempted_count else "completed")
    )
    return run.model_copy(
        update={
            "status": status,
            "finished_at": datetime.now(UTC),
            "reason": None if status == "completed" else status,
        }
    )
