import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from experiments.comparison import ComparisonConfig, assert_comparable, run_pair
from experiments.harness import ScenarioHarness
from experiments.observations import PairResult
from experiments.security_scenarios import load_scenarios
from tests.integration.search_fixtures import configure_search

from rag_access_guard_api.server import create_event_loop


def test_revoked_history_differs_only_by_policy_gates(
    comparison_harness: ScenarioHarness, comparison_config: ComparisonConfig
) -> None:
    case = next(
        item
        for item in load_scenarios(Path("experiments/scenarios.json"))
        if item.id == "history-after-direct-revoke"
    )
    with asyncio.Runner(loop_factory=create_event_loop) as runner:
        pair = runner.run(run_pair(case, comparison_config, comparison_harness))
    assert_comparable(pair)
    assert pair.baseline.system_context_violation
    assert not pair.guarded.system_context_violation
    assert pair.guarded.forbidden_release_count == 0
    assert pair.baseline.renderer_identity == pair.guarded.renderer_identity


@pytest.fixture
def history_pair(
    comparison_harness: ScenarioHarness, comparison_config: ComparisonConfig
) -> PairResult:
    case = next(
        c
        for c in load_scenarios(Path("experiments/scenarios.json"))
        if c.id == "history-after-direct-revoke"
    )
    with asyncio.Runner(loop_factory=create_event_loop) as runner:
        return runner.run(run_pair(case, comparison_config, comparison_harness))


def test_repeated_reads_differ_only_by_policy_gates(history_pair: PairResult) -> None:
    for surface in ("stored_read", "source_read"):
        baseline = next(o for o in history_pair.baseline.observations if o.surface == surface)
        guarded = next(o for o in history_pair.guarded.observations if o.surface == surface)
        assert baseline.violation
        assert baseline.body
        assert baseline.titles
        assert not guarded.violation
        assert not guarded.body
        assert not guarded.titles
        assert not guarded.source_refs
        assert guarded.cache_control == "private, no-store"
    source = next(o for o in history_pair.guarded.observations if o.surface == "source_read")
    assert source.http_status == 404


def test_source_denial_matches_unknown_identity(
    history_pair: PairResult,
    comparison_harness: ScenarioHarness,
    comparison_config: ComparisonConfig,
) -> None:
    case = next(
        c for c in load_scenarios(Path("experiments/scenarios.json")) if c.id == "unknown-source"
    )
    with asyncio.Runner(loop_factory=create_event_loop) as runner:
        unknown = runner.run(run_pair(case, comparison_config, comparison_harness))
    denied = next(o for o in history_pair.guarded.observations if o.surface == "source_read")
    missing = next(o for o in unknown.guarded.observations if o.surface == "source_read")
    assert denied.http_status == missing.http_status == 404
    assert denied.response_body == missing.response_body == '{"detail":"Not found"}'
    assert denied.cache_control == missing.cache_control == "private, no-store"


@pytest.mark.parametrize("field", ["corpus_hash", "config_hash", "tokenizer_identity"])
def test_different_inputs_invalidate_pair(history_pair: PairResult, field: str) -> None:
    changed = replace(history_pair, guarded=replace(history_pair.guarded, **{field: "different"}))
    with pytest.raises(ValueError, match="Incomparable"):
        assert_comparable(changed)


def test_false_tokenizer_identity_rejected_before_database(
    comparison_harness: ScenarioHarness,
) -> None:
    case = load_scenarios(Path("experiments/scenarios.json"))[0]
    with (
        asyncio.Runner(loop_factory=create_event_loop) as runner,
        pytest.raises(ValueError, match="identity"),
    ):
        _ = runner.run(
            run_pair(case, ComparisonConfig(tokenizer_identity="different"), comparison_harness)
        )
    assert not comparison_harness.databases.owned


def test_actual_retrieval_recipe_changes_comparison_identity(
    comparison_harness: ScenarioHarness,
    comparison_config: ComparisonConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = load_scenarios(Path("experiments/scenarios.json"))[0]
    with asyncio.Runner(loop_factory=create_event_loop) as runner:
        first = runner.run(run_pair(case, comparison_config, comparison_harness))
        configure_search(tmp_path / "changed-retrieval.json", monkeypatch, threshold=0.1)
        second = runner.run(run_pair(case, comparison_config, comparison_harness))
    assert first.guarded.config_hash != second.guarded.config_hash
