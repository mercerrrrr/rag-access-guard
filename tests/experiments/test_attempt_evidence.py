import asyncio
from pathlib import Path

import pytest
from experiments.comparison import ComparisonConfig, run_pair
from experiments.harness import ScenarioHarness
from experiments.host import ContextModel
from experiments.security_scenarios import load_scenarios

from rag_access_guard_api.server import create_event_loop


def test_every_retry_has_independent_stage_timings(
    comparison_harness: ScenarioHarness,
    comparison_config: ComparisonConfig,
) -> None:
    case = next(
        c
        for c in load_scenarios(Path("experiments/scenarios.json"))
        if c.id == "revoke-before-release"
    )
    with asyncio.Runner(loop_factory=create_event_loop) as runner:
        pair = runner.run(run_pair(case, comparison_config, comparison_harness))
    attempts = pair.guarded.attempts
    assert len(attempts) == 2
    assert [a.status for a in attempts] == ["stale_discarded", "completed"]
    assert [a.model_called for a in attempts] == [True, False]
    for attempt in attempts:
        timings = dict(attempt.stage_ms)
        assert {"query_embedding", "retrieval", "prepare", "release"} <= timings.keys()
        assert all(value >= 0 for value in timings.values())
        assert sum(timings.values()) <= attempt.elapsed_ms


def test_failed_model_attempt_retains_actual_request(
    comparison_harness: ScenarioHarness,
    comparison_config: ComparisonConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unavailable(
        self: ContextModel,
        *,
        user_input: str,
        system_supplied_context: str,
    ) -> str:
        del self, user_input, system_supplied_context
        message = "Synthetic model failure"
        raise RuntimeError(message)

    monkeypatch.setattr(ContextModel, "generate", unavailable)
    case = load_scenarios(Path("experiments/scenarios.json"))[0]

    async def run() -> None:
        async with comparison_harness.open_pair(case, comparison_config) as pair:
            with pytest.raises(ExceptionGroup):
                _ = await pair.run_arm(case, comparison_config, "guarded")
            records = pair.arm_records
            assert len(records) == 1
            record = records[0]
            assert record.status == "failed"
            assert record.result.attempts[0].status == "failed"
            assert record.result.attempts[0].model_called
            assert record.result.attempts[0].elapsed_ms > 0
            assert any(o.request is not None for o in record.result.observations)
            assert not any(o.surface == "release" for o in record.result.observations)

    with asyncio.Runner(loop_factory=create_event_loop) as runner:
        runner.run(run())
    assert not comparison_harness.databases.owned
