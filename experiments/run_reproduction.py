"""Validate persisted evidence before admitting a prior run for reproduction."""

from hashlib import sha256
from pathlib import Path

from experiments.run_manifest import RunCounts, RunManifest
from experiments.trials import TrialRecord


def load_reproduction(path: Path) -> RunManifest:
    """A completed label alone does not prove complete, untampered pair records."""
    manifest = RunManifest.model_validate_json(path.read_bytes())
    raw = path.with_name("records.jsonl").read_bytes()
    records = tuple(TrialRecord.model_validate_json(line) for line in raw.splitlines())
    counts = RunCounts(
        scheduled=len(manifest.schedule),
        completed=sum(record.status == "completed" for record in records),
        failed=sum(record.status == "failed" for record in records),
        invalid=sum(record.status == "invalid" for record in records),
        attempts=sum(len(arm.result.attempts) for record in records for arm in record.arms),
    )
    if (
        manifest.status != "completed"
        or manifest.dirty
        or manifest.finished_at is None
        or not records
        or sha256(raw).hexdigest() != manifest.records_sha256
        or tuple(record.trial for record in records) != manifest.schedule
        or counts != manifest.counts
        or any(
            record.status != "completed"
            or record.pair is None
            or len(record.arms) != len(record.trial.order)
            or tuple(arm.result.arm for arm in record.arms) != record.trial.order
            or any(arm.status != "completed" for arm in record.arms)
            for record in records
        )
    ):
        message = "Reproduction records are incomplete or inconsistent"
        raise ValueError(message)
    return manifest
