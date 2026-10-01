from pathlib import Path

import pytest
from experiments.run_config import RunConfig, RunOptions
from experiments.run_setup import RunInputs
from experiments.scenario_types import Scenario
from experiments.security_scenarios import load_scenarios

from experiments import run_setup


def options(tmp_path: Path) -> RunOptions:
    path = tmp_path / "config.json"
    _ = path.write_text(RunConfig().model_dump_json(), encoding="utf-8")
    return RunOptions(
        scenarios=Path("experiments/scenarios.json"),
        config=path,
        seed=7,
        repetitions=1,
        llm="fake",
        output=tmp_path / "run",
        reproduce=None,
    )


def test_live_selection_does_not_erase_invalid_config(tmp_path: Path) -> None:
    selected = options(tmp_path).model_copy(update={"llm": "ollama"})
    config = RunConfig()
    changed = config.model_copy(
        update={
            "comparison": config.comparison.model_copy(update={"tokenizer_identity": "wrong"}),
        }
    )
    _ = selected.config.write_text(changed.model_dump_json(), encoding="utf-8")
    with pytest.raises(ValueError, match="identity"):
        _ = RunInputs.load(selected, Path.cwd())


def test_input_changes_during_loading_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = options(tmp_path)
    original = load_scenarios

    def change_config(path: Path) -> tuple[Scenario, ...]:
        _ = selected.config.write_text(
            RunConfig(warmup_repetitions=3).model_dump_json(),
            encoding="utf-8",
        )
        return original(path)

    monkeypatch.setattr(run_setup, "load_scenarios", change_config)
    with pytest.raises(ValueError, match="changed"):
        _ = RunInputs.load(selected, Path.cwd())
