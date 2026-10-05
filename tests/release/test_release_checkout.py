import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from scripts.verify_release import validate_checkout
from tests.release.test_release_contract import complete_proof


def git(repository: Path, *arguments: str) -> str:
    executable = shutil.which("git")
    assert executable is not None
    return subprocess.run(  # noqa: S603 -- fixed Git commands in disposable test repositories.
        [
            executable,
            "-C",
            str(repository),
            "-c",
            "user.name=mercerrrrr",
            "-c",
            "user.email=vladimir260702@gmail.com",
            *arguments,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()


def checkout(tmp_path: Path) -> str:
    _ = git(tmp_path, "init", "--initial-branch=main")
    _ = (tmp_path / "README.md").write_text("Synthetic release fixture", encoding="utf-8")
    _ = git(tmp_path, "add", "README.md")
    _ = git(tmp_path, "commit", "-m", "test: create release fixture")
    _ = git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    return git(tmp_path, "rev-parse", "HEAD")


@pytest.mark.parametrize("damage", ["sha", "dirty", "untracked", "private"])
def test_release_refuses_unverifiable_checkout(tmp_path: Path, damage: str) -> None:
    sha = checkout(tmp_path)
    proof = replace(complete_proof(), commit_sha=sha, remote_sha=sha, ci_sha=sha)
    match damage:
        case "sha":
            proof = replace(proof, commit_sha="b" * 40)
        case "dirty":
            _ = (tmp_path / "README.md").write_text("Changed", encoding="utf-8")
        case "untracked":
            _ = (tmp_path / "extra.py").write_text("value = 1", encoding="utf-8")
        case "private":
            _ = (tmp_path / "private.md").write_text("Private", encoding="utf-8")
            _ = git(tmp_path, "add", "private.md")
            _ = git(tmp_path, "commit", "-m", "test: private boundary fixture")
            _ = git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
            sha = git(tmp_path, "rev-parse", "HEAD")
            proof = replace(proof, commit_sha=sha, remote_sha=sha, ci_sha=sha)
        case _:
            pytest.fail("Unknown damage")
    assert validate_checkout(tmp_path, proof)


def test_release_accepts_clean_matching_checkout(tmp_path: Path) -> None:
    sha = checkout(tmp_path)
    proof = replace(complete_proof(), commit_sha=sha, remote_sha=sha, ci_sha=sha)
    assert validate_checkout(tmp_path, proof) == ()
