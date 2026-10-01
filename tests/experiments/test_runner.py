import asyncio
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from experiments.comparison_config import ComparisonConfig
from experiments.fixture_manifest import load_fixture_manifest
from experiments.harness import ScenarioHarness
from experiments.host import ContextModel
from experiments.run_manifest import RunIdentity, RunManifest, validate_reproduction
from experiments.run_reproduction import load_reproduction
from experiments.run_schedule import schedule_trials
from experiments.runner import RunExecution
from experiments.security_scenarios import load_scenarios
from experiments.trials import TrialRecord

from rag_access_guard_api.server import create_event_loop


def make_manifest(case_id: str, *, dirty: bool = False) -> RunManifest:
    config = ComparisonConfig()
    schedule = schedule_trials((case_id,), seed=7, repetitions=1, warmups=0)
    return RunManifest(
        run_id=uuid4(),
        status="running",
        started_at=datetime.now(UTC),
        identity=RunIdentity(
            git_sha="a" * 40,
            config={},
            corpus_hash=sha256(
                load_fixture_manifest(Path("experiments/fixture_manifest.json"))
                .model_dump_json()
                .encode()
            ).hexdigest(),
            scenario_hash="c" * 64,
            lock_hashes={},
            model_tag="synthetic",
            model_digest="d" * 64,
            runtime_config_hash="e" * 64,
            trial_config_hashes={
                trial.trial_id: config.model_copy(update={"seed": trial.seed}).runtime_digest()
                for trial in schedule
            },
            runtime_versions={},
        ),
        dirty=dirty,
        environment={},
        schedule=schedule,
    )


@pytest.mark.parametrize("failure", [False, True])
def test_runner_retains_outcome_and_attempt_counts(
    *,
    failure: bool,
    comparison_harness: ScenarioHarness,
    comparison_config: ComparisonConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if failure:

        async def fail(self: ContextModel, *, user_input: str, system_supplied_context: str) -> str:
            del self, user_input, system_supplied_context
            message = "private model failure"
            raise RuntimeError(message)

        monkeypatch.setattr(ContextModel, "generate", fail)
    case = load_scenarios(Path("experiments/scenarios.json"))[0]
    manifest = make_manifest(case.id)
    execution = RunExecution(
        manifest, comparison_config, {case.id: case}, comparison_harness, tmp_path
    )
    with asyncio.Runner(loop_factory=create_event_loop) as runner:
        result = runner.run(execution.run())
    assert result.status == ("failed" if failure else "completed")
    assert result.counts.scheduled == 1
    assert result.counts.failed == int(failure)
    assert result.counts.completed == int(not failure)
    assert result.counts.attempts >= 1
    record = TrialRecord.model_validate_json((tmp_path / "records.jsonl").read_bytes())
    assert record.status == result.status
    assert "private model failure" not in (tmp_path / "records.jsonl").read_text()


def test_dirty_run_is_diagnostic_only(
    comparison_harness: ScenarioHarness,
    comparison_config: ComparisonConfig,
    tmp_path: Path,
) -> None:
    case = load_scenarios(Path("experiments/scenarios.json"))[0]
    execution = RunExecution(
        make_manifest(case.id, dirty=True),
        comparison_config,
        {case.id: case},
        comparison_harness,
        tmp_path,
    )
    with asyncio.Runner(loop_factory=create_event_loop) as runner:
        result = runner.run(execution.run())
    assert result.status == "invalid"
    assert result.reason == "dirty_code_diagnostic_only"


def test_input_drift_blocks_completed_status(
    comparison_harness: ScenarioHarness,
    comparison_config: ComparisonConfig,
    tmp_path: Path,
) -> None:
    case = load_scenarios(Path("experiments/scenarios.json"))[0]
    execution = RunExecution(
        make_manifest(case.id),
        comparison_config,
        {case.id: case},
        comparison_harness,
        tmp_path,
        verify_inputs=lambda: False,
    )
    with asyncio.Runner(loop_factory=create_event_loop) as runner:
        result = runner.run(execution.run())
    assert result.status == "invalid"
    assert result.reason == "input_drift"


def test_interrupt_preserves_partial_attempt_and_stops_schedule(
    comparison_harness: ScenarioHarness,
    comparison_config: ComparisonConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def cancel(self: ContextModel, *, user_input: str, system_supplied_context: str) -> str:
        del self, user_input, system_supplied_context
        raise asyncio.CancelledError

    monkeypatch.setattr(ContextModel, "generate", cancel)
    case = load_scenarios(Path("experiments/scenarios.json"))[0]
    manifest = make_manifest(case.id).model_copy(
        update={
            "schedule": schedule_trials((case.id,), seed=7, repetitions=2, warmups=0),
        }
    )
    execution = RunExecution(
        manifest,
        comparison_config,
        {case.id: case},
        comparison_harness,
        tmp_path,
    )
    with asyncio.Runner(loop_factory=create_event_loop) as runner:
        result = runner.run(execution.run())
    assert result.status == "failed"
    assert result.counts.scheduled == 2
    assert result.counts.failed == 1
    records = (tmp_path / "records.jsonl").read_bytes().splitlines()
    assert len(records) == 1
    record = TrialRecord.model_validate_json(records[0])
    assert record.error_code == "interrupted"
    assert record.arms[0].result.attempts[0].status == "failed"


def test_two_fake_runs_preserve_structural_outcomes(
    comparison_harness: ScenarioHarness,
    comparison_config: ComparisonConfig,
    tmp_path: Path,
) -> None:
    case = load_scenarios(Path("experiments/scenarios.json"))[0]
    first_path, second_path = tmp_path / "first", tmp_path / "second"
    first_path.mkdir()
    second_path.mkdir()
    first = make_manifest(case.id)
    second = first.model_copy(update={"run_id": uuid4(), "reproduces_run_id": first.run_id})
    with asyncio.Runner(loop_factory=create_event_loop) as runner:
        for manifest, path in ((first, first_path), (second, second_path)):
            execution = RunExecution(
                manifest,
                comparison_config,
                {case.id: case},
                comparison_harness,
                path,
            )
            result = runner.run(execution.run())
            assert result.status == "completed"
    original = load_reproduction(first_path / "manifest.json")
    repeated = load_reproduction(second_path / "manifest.json")
    validate_reproduction(original, repeated)
    assert original.run_id != repeated.run_id
    first_record = TrialRecord.model_validate_json((first_path / "records.jsonl").read_bytes())
    second_record = TrialRecord.model_validate_json((second_path / "records.jsonl").read_bytes())
    assert first_record.trial == second_record.trial
    for left, right in zip(first_record.arms, second_record.arms, strict=True):
        assert left.status == right.status
        assert left.result.arm == right.result.arm
        assert [
            (o.action_id, o.surface, o.violation, len(o.source_refs), len(o.forbidden_refs), o.body)
            for o in left.result.observations
        ] == [
            (o.action_id, o.surface, o.violation, len(o.source_refs), len(o.forbidden_refs), o.body)
            for o in right.result.observations
        ]
