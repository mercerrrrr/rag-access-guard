import asyncio
from pathlib import Path

import pytest
from experiments.comparison_config import ComparisonConfig
from experiments.harness import ScenarioHarness
from experiments.host import ContextModel
from experiments.run_schedule import schedule_trials
from experiments.security_scenarios import load_scenarios
from experiments.trials import execute_trial

from rag_access_guard_api.server import create_event_loop


def test_failed_attempt_is_preserved(
    comparison_harness: ScenarioHarness,
    comparison_config: ComparisonConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail(self: ContextModel, *, user_input: str, system_supplied_context: str) -> str:
        del self, user_input, system_supplied_context
        message = "private-connection-secret"
        raise RuntimeError(message)

    monkeypatch.setattr(ContextModel, "generate", fail)
    case = load_scenarios(Path("experiments/scenarios.json"))[0]
    trial = schedule_trials((case.id,), seed=7, repetitions=1, warmups=0)[0]
    with asyncio.Runner(loop_factory=create_event_loop) as runner:
        record = runner.run(execute_trial(trial, case, comparison_config, comparison_harness))
    assert record.status == "failed"
    assert record.pair is None
    assert len(record.arms) == 1
    assert record.arms[0].result.attempts[0].status == "failed"
    assert "private-connection-secret" not in record.model_dump_json()
    assert not comparison_harness.databases.owned
