from pathlib import Path

from experiments.harness import ScenarioHarness
from experiments.run_environment import database_facts, git_output, git_state


def test_untracked_executable_marks_run_dirty(tmp_path: Path) -> None:
    _ = git_output(tmp_path, "init", "--initial-branch=main")
    _ = (tmp_path / "main.py").write_text("value = 1\n")
    _ = git_output(tmp_path, "add", "main.py")
    repository = Path(__file__).resolve().parents[2]
    name = git_output(repository, "log", "-1", "--format=%an")
    email = git_output(repository, "log", "-1", "--format=%ae")
    _ = git_output(
        tmp_path,
        "-c",
        f"user.name={name}",
        "-c",
        f"user.email={email}",
        "commit",
        "-m",
        "test: seed disposable checkout",
    )
    assert not git_state(tmp_path).dirty
    _ = (tmp_path / "influencing.py").write_text("value = 2\n")
    assert git_state(tmp_path).dirty


def test_database_identity_is_observed_from_the_server(comparison_harness: ScenarioHarness) -> None:
    facts = database_facts(comparison_harness.databases)
    assert int(facts.get("postgres_version_num", "0")) >= 180000
    assert facts.get("pgvector_available") == "0.8.6"
