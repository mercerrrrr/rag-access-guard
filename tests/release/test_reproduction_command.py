from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest
from experiments.run_manifest import RunManifest
from experiments.summary_types import Summary
from tests.experiments.summary_fixtures import paired_record, persist_run
from tests.release.test_release_reproduction import summary

from experiments import verify_reproduction as verifier


def test_disk_verifier_loads_both_verified_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = persist_run(tmp_path, paired_record())
    repeated = original.model_copy(
        update={"run_id": UUID(int=2), "reproduces_run_id": original.run_id}
    )
    first = summary(tmp_path)
    second = replace(first, run_id=str(repeated.run_id))
    reads: list[Path] = []
    original_path, repeated_path = tmp_path / "first", tmp_path / "second"
    for path in (original_path, repeated_path):
        path.mkdir()
        _ = (path / "records.jsonl").write_text(
            paired_record().model_dump_json() + "\n", encoding="utf-8"
        )

    def manifest(path: Path) -> RunManifest:
        reads.append(path)
        return original if path.parent == original_path else repeated

    def aggregate(path: Path, scenarios: Path) -> Summary:
        assert scenarios == tmp_path / "experiments/scenarios.json"
        reads.append(path)
        return first if path == original_path else second

    monkeypatch.setattr(verifier, "load_reproduction", manifest)
    monkeypatch.setattr(verifier, "load_summary", aggregate)

    def cases(_: Path) -> tuple[str]:
        return ("allowed",)

    monkeypatch.setattr(verifier, "load_scenarios", cases)
    result = verifier.verify_runs(original_path, repeated_path, tmp_path, "d" * 40)
    assert result == first
    assert reads == [
        original_path / "manifest.json",
        repeated_path / "manifest.json",
        original_path,
        repeated_path,
    ]


def test_reproduction_command_rejects_missing_evidence(tmp_path: Path) -> None:
    assert (
        verifier.main(
            [
                "--original",
                str(tmp_path / "missing"),
                "--repeated",
                str(tmp_path / "missing-repeat"),
                "--repository",
                str(tmp_path),
            ]
        )
        == 2
    )
