import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from experiments.run_manifest import RunManifest
from experiments.security_scenarios import load_scenarios

from rag_access_guard_api.services.model_manifest import MODEL_DIGEST


def test_cli_help_exposes_reproducible_controls() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "experiments.run", "--help"],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0
    for option in (
        "--scenarios",
        "--config",
        "--seed",
        "--repetitions",
        "--llm",
        "--output",
        "--reproduce",
    ):
        assert option in result.stdout


@pytest.mark.parametrize("repetitions", ["0", "-1"])
def test_cli_rejects_invalid_count_before_creating_output(tmp_path: Path, repetitions: str) -> None:
    output = tmp_path / "run"
    result = subprocess.run(  # noqa: S603 -- fixed interpreter and parametrized test arguments.
        [
            sys.executable,
            "-m",
            "experiments.run",
            "--repetitions",
            repetitions,
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 2
    assert not output.exists()


def test_cli_records_offline_database_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "RAG_ACCESS_GUARD_DATABASE_URL",
        "postgresql+psycopg://unavailable:unused@127.0.0.1:1/rag_access_guard",
    )
    source = Path("experiments")
    _ = shutil.copytree(source / "fixtures", tmp_path / "fixtures")
    _ = shutil.copyfile(source / "fixture_manifest.json", tmp_path / "fixture_manifest.json")
    case = load_scenarios(source / "scenarios.json")[0]
    scenarios = tmp_path / "scenarios.json"
    _ = scenarios.write_text("[" + case.model_dump_json() + "]", encoding="utf-8")
    output = tmp_path / "run"
    result = subprocess.run(  # noqa: S603 -- fixed interpreter and owned temporary paths.
        [
            sys.executable,
            "-m",
            "experiments.run",
            "--scenarios",
            str(scenarios),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=40,
    )
    assert result.returncode != 0
    manifest = RunManifest.model_validate_json((output / "manifest.json").read_bytes())
    assert manifest.status == "failed"
    assert manifest.counts.failed == 1
    assert manifest.counts.completed == 0
    assert "unused" not in (output / "records.jsonl").read_text()


def test_missing_live_tokenizer_is_not_a_synthetic_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_ACCESS_GUARD_MODEL_TOKENIZER_PATH", str(tmp_path / "missing.json"))
    output = tmp_path / "live"
    result = subprocess.run(  # noqa: S603 -- fixed interpreter and owned temporary output.
        [sys.executable, "-m", "experiments.run", "--llm", "ollama", "--output", str(output)],
        capture_output=True,
        text=True,
        check=False,
        timeout=40,
    )
    assert result.returncode != 0
    manifest = RunManifest.model_validate_json((output / "manifest.json").read_bytes())
    assert manifest.status == "not_run"
    assert manifest.live_status == "not_run"
    assert manifest.identity.model_tag == "qwen3:4b"
    assert manifest.identity.model_digest == MODEL_DIGEST
    assert manifest.counts.completed == 0
