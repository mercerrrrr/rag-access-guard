from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest
from experiments.run_manifest import RunCounts
from experiments.run_reproduction import load_reproduction
from experiments.trials import TrialRecord
from tests.experiments.test_runner import make_manifest


@pytest.mark.parametrize("damage", ["hash", "partial", "missing_pair", "duplicate"])
def test_reproduction_rejects_damaged_records(tmp_path: Path, damage: str) -> None:
    manifest = make_manifest("case")
    record = TrialRecord(
        trial=manifest.schedule[0],
        status="failed",
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        elapsed_ms=1,
        error_code="execution_failed",
    )
    raw = (record.model_dump_json() + "\n").encode()
    if damage == "duplicate":
        raw += raw
    elif damage == "partial":
        raw = b""
    original = manifest.model_copy(
        update={
            "status": "completed",
            "finished_at": datetime.now(UTC),
            "counts": RunCounts(scheduled=1, completed=1),
            "records_sha256": "0" * 64 if damage == "hash" else sha256(raw).hexdigest(),
        }
    )
    _ = (tmp_path / "records.jsonl").write_bytes(raw)
    path = tmp_path / "manifest.json"
    _ = path.write_text(original.model_dump_json())
    with pytest.raises(ValueError, match="records"):
        _ = load_reproduction(path)
