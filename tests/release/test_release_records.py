from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest
from scripts.verify_release import EvidenceRecord, validate_records


def evidence(tmp_path: Path) -> tuple[EvidenceRecord, ...]:
    reports: list[EvidenceRecord] = []
    for kind in ("ci", "wheel_install", "demo", "reproduction", "clean_checks", "review"):
        path = tmp_path / f"{kind}.txt"
        raw = f"observed {kind}".encode()
        _ = path.write_bytes(raw)
        reports.append(EvidenceRecord(kind, "a" * 40, "pass", path, sha256(raw).hexdigest()))
    return tuple(reports)


@pytest.mark.parametrize("damage", ["missing", "duplicate", "sha", "status", "hash", "file"])
def test_release_records_require_complete_unchanged_evidence(tmp_path: Path, damage: str) -> None:
    records = evidence(tmp_path)
    match damage:
        case "missing":
            records = records[:-1]
        case "duplicate":
            records = (*records, records[0])
        case "sha":
            records = (replace(records[0], commit_sha="b" * 40), *records[1:])
        case "status":
            records = (replace(records[0], status="not_run"), *records[1:])
        case "hash":
            records = (replace(records[0], sha256="0" * 64), *records[1:])
        case "file":
            records = (replace(records[0], path=tmp_path / "missing.txt"), *records[1:])
        case _:
            pytest.fail("Unknown damage")
    assert validate_records(records, "a" * 40)


def test_release_records_accept_complete_observed_reports(tmp_path: Path) -> None:
    assert validate_records(evidence(tmp_path), "a" * 40) == ()
