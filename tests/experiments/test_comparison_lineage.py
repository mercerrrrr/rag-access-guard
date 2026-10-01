import asyncio
from pathlib import Path

import pytest
from experiments.comparison import ComparisonConfig, run_pair
from experiments.harness import ScenarioHarness
from experiments.security_scenarios import load_scenarios

from rag_access_guard_api.server import create_event_loop


@pytest.mark.parametrize("case_id", ["paste-user-only", "paste-both-channels", "paste-second-hop"])
def test_observed_user_origins_are_preserved(
    case_id: str,
    comparison_harness: ScenarioHarness,
    comparison_config: ComparisonConfig,
) -> None:
    case = next(c for c in load_scenarios(Path("experiments/scenarios.json")) if c.id == case_id)
    with asyncio.Runner(loop_factory=create_event_loop) as runner:
        pair = runner.run(run_pair(case, comparison_config, comparison_harness))
    for arm in (pair.baseline, pair.guarded):
        observed = [o for o in arm.observations if o.request is not None]
        assert observed
        assert all(o.markers for o in observed)
        assert any(b.user_origin_marker for o in observed for _, b in o.markers)
        if case_id == "paste-second-hop":
            assert any(
                b.user_origin_marker and not b.user_marker_present for _, b in observed[-1].markers
            )
    if case_id == "paste-both-channels":
        assert pair.baseline.system_context_violation
        assert not pair.guarded.system_context_violation


@pytest.mark.parametrize("case_id", ["revoke-before-release", "release-before-revoke"])
def test_actual_commit_order_matches_barrier(
    case_id: str,
    comparison_harness: ScenarioHarness,
    comparison_config: ComparisonConfig,
    caplog: pytest.LogCaptureFixture,
) -> None:
    case = next(c for c in load_scenarios(Path("experiments/scenarios.json")) if c.id == case_id)
    with asyncio.Runner(loop_factory=create_event_loop) as runner:
        pair = runner.run(run_pair(case, comparison_config, comparison_harness))
    ask, revoke = case.actions[:2]
    expected = (revoke.id, ask.id) if case_id == "revoke-before-release" else (ask.id, revoke.id)
    assert pair.baseline.commit_order == pair.guarded.commit_order == expected
    assert pair.guarded.forbidden_release_count == 0
    assert pair.baseline.forbidden_release_count == (case_id == "revoke-before-release")
    if case_id == "revoke-before-release":
        release = next(o for o in pair.guarded.observations if o.surface == "release")
        assert release.persisted == "neutral"
        assert not release.body
        assert not release.source_refs
        assert "SYNTHETIC_" not in release.response_body
        assert "SYNTHETIC_" not in caplog.text
        attempts = [o for o in pair.guarded.observations if o.surface == "model_context"]
        assert [o.attempt_status for o in attempts] == ["stale_discarded", "completed"]
        assert all(o.attempt_elapsed_ms is not None and o.attempt_elapsed_ms > 0 for o in attempts)
