import json
from hashlib import sha256
from pathlib import Path

import pytest
from scripts.release_boundary import validate_public_tree
from scripts.verify_release import read_verified, validate_ci


@pytest.mark.parametrize(
    "path",
    [
        "planning/phase.md",
        "apps/web/README.md",
        ".env",
        "experiments/run.jsonl",
        "artifacts/screen.png",
        "documents/thesis.docx",
        "models/weights.gguf",
        ".codex/config.toml",
        "private/secret.txt",
    ],
)
def test_public_tree_contains_no_private_artifacts(path: str) -> None:
    assert validate_public_tree(("README.md", "apps/api/main.py", path))


def test_public_tree_accepts_declared_project_sources() -> None:
    assert (
        validate_public_tree(
            (
                "README.md",
                ".env.example",
                ".github/workflows/ci.yml",
                "experiments/fixtures/a.txt",
                "typings/pgvector/__init__.pyi",
            )
        )
        == ()
    )


def test_artifact_checksum_rejects_changed_bytes(tmp_path: Path) -> None:
    artifact = tmp_path / "proof.json"
    _ = artifact.write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        _ = read_verified(artifact, sha256(b"original").hexdigest())


def test_artifact_checksum_accepts_exact_bytes(tmp_path: Path) -> None:
    artifact = tmp_path / "proof.json"
    _ = artifact.write_bytes(b"original")
    assert read_verified(artifact, sha256(b"original").hexdigest()) == b"original"


def ci_report() -> dict[str, object]:
    return {
        "headSha": "a" * 40,
        "status": "completed",
        "conclusion": "success",
        "jobs": [
            {"name": name, "status": "completed", "conclusion": "success"}
            for name in ("Python and API", "Web", "Browser security workflow")
        ],
    }


@pytest.mark.parametrize("damage", ["sha", "missing", "skipped", "running", "duplicate"])
def test_ci_proof_rejects_incomplete_or_foreign_jobs(damage: str) -> None:
    report = ci_report()
    match damage:
        case "sha":
            report["headSha"] = "b" * 40
        case "missing":
            report["jobs"] = []
        case "skipped":
            report["jobs"] = [
                {"name": name, "status": "completed", "conclusion": "skipped"}
                for name in ("Python and API", "Web", "Browser security workflow")
            ]
        case "running":
            report["status"] = "in_progress"
        case "duplicate":
            report["jobs"] = [{"name": "Web", "status": "completed", "conclusion": "success"}] * 3
        case _:
            pytest.fail("Unknown damage")
    assert validate_ci(json.dumps(report).encode(), "a" * 40)


def test_ci_proof_accepts_all_required_successful_jobs() -> None:
    assert validate_ci(json.dumps(ci_report()).encode(), "a" * 40) == ()
