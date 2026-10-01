import asyncio
from pathlib import Path

import pytest
from experiments.comparison import ComparisonConfig, run_pair
from experiments.harness import ScenarioHarness
from experiments.scenario_types import Scenario
from experiments.security_scenarios import load_scenarios

from rag_access_guard_api.server import create_event_loop

CASES = load_scenarios(Path("experiments/scenarios.json"))


@pytest.mark.parametrize("case", CASES, ids=[case.id for case in CASES])
def test_guarded_scenario_surfaces(
    case: Scenario,
    comparison_harness: ScenarioHarness,
    comparison_config: ComparisonConfig,
) -> None:
    with asyncio.Runner(loop_factory=create_event_loop) as runner:
        pair = runner.run(run_pair(case, comparison_config, comparison_harness))
    for expected in case.expected.checks:
        observed = [
            o
            for o in pair.guarded.observations
            if o.action_id == expected.action_id and o.surface == expected.surface
        ]
        assert observed, (case.id, expected.action_id, expected.surface)
        assert all(not o.violation for o in observed)
        if expected.surface != "document_list":
            documents = dict(pair.guarded.documents)
            exposed = (
                [o for o in observed if o.request is not None]
                if expected.surface == "model_context"
                else observed[-1:]
            )
            assert {ref.document_id for o in exposed for ref in o.source_refs} == {
                documents[key] for key in expected.documents
            }, (case.id, expected.action_id, expected.surface)
        if expected.surface == "model_context":
            assert sum(o.request is not None for o in observed) == expected.llm_calls
        else:
            assert observed[-1].http_status == expected.http_status
            assert observed[-1].cache_control == "private, no-store"
            assert observed[-1].persisted == expected.persisted
        if expected.authorization in {"deny", "neutral"}:
            assert not observed[-1].body
            assert not observed[-1].titles
            assert not observed[-1].source_refs
    assert not comparison_harness.databases.owned
