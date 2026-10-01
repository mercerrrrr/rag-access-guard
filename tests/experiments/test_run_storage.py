import os
from pathlib import Path

import pytest
from experiments.run_storage import atomic_write, create_run_directory


def test_refuses_output_inside_public_repo(tmp_path: Path) -> None:
    repo = tmp_path / "public"
    repo.mkdir()
    with pytest.raises(ValueError, match="outside"):
        _ = create_run_directory(repo / "results", repo)
    assert not (repo / "results").exists()


def test_refuses_existing_run_directory(tmp_path: Path) -> None:
    with pytest.raises(FileExistsError):
        _ = create_run_directory(tmp_path, tmp_path / "public")


def test_refuses_relative_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="absolute"):
        _ = create_run_directory(Path("relative"), tmp_path / "public")


def test_private_output_is_new_directory(tmp_path: Path) -> None:
    output = tmp_path / "private" / "run"
    assert create_run_directory(output, tmp_path / "public") == output.resolve()
    assert output.is_dir()


def test_atomic_write_preserves_old_record_on_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "manifest.json"
    _ = target.write_bytes(b"original")

    def interrupted(source: object, destination: object) -> None:
        del source, destination
        message = "Simulated interruption"
        raise OSError(message)

    monkeypatch.setattr(os, "replace", interrupted)
    with pytest.raises(OSError, match="interruption"):
        atomic_write(target, b"new record")
    assert target.read_bytes() == b"original"
