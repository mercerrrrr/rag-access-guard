import asyncio
from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest
from experiments.comparison import run_pair
from experiments.comparison_config import ComparisonConfig
from experiments.database import ExperimentDatabases
from experiments.harness import PairHarness, ScenarioHarness
from experiments.host import ContextModel
from experiments.host_state import HostState, PendingAnswer
from experiments.observations import Arm, ArmResult
from experiments.runner import RunExecution
from experiments.scenario_types import Scenario
from experiments.security_scenarios import load_scenarios
from experiments.seed import SyntheticEmbedder
from tests.experiments.test_runner import make_manifest
from tests.integration.search_fixtures import configure_search

from rag_access_guard_api.schemas.chat import MessageResponse
from rag_access_guard_api.server import create_event_loop


def test_synthetic_fixture_scores_do_not_depend_on_random_ids() -> None:
    async def probe() -> None:
        embedder = SyntheticEmbedder()
        vectors = await embedder.embed_passages(("public", "staff", "teacher"))
        assert len({vector[0] for vector in vectors}) == 3
        assert all(vector[0] > 0.8 for vector in vectors)
        assert vectors == await embedder.embed_passages(("public", "staff", "teacher"))

    asyncio.run(probe())


@pytest.mark.parametrize("drift", ["recipe", "corpus"])
def test_completed_arms_must_match_admitted_identity(
    drift: str,
    comparison_harness: ScenarioHarness,
    comparison_config: ComparisonConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = load_scenarios(Path("experiments/scenarios.json"))[0]
    manifest = make_manifest(case.id)
    if drift == "recipe":
        configure_search(tmp_path / "changed.json", monkeypatch, threshold=0.9)
    else:
        original = PairHarness.run_arm

        async def changed(
            self: PairHarness, case: Scenario, config: ComparisonConfig, arm: Arm
        ) -> ArmResult:
            result = replace(await original(self, case, config, arm), corpus_hash="f" * 64)
            self.arm_records[-1] = replace(self.arm_records[-1], result=result)
            return result

        monkeypatch.setattr(PairHarness, "run_arm", changed)
    with asyncio.Runner(loop_factory=create_event_loop) as runner:
        result = runner.run(
            RunExecution(
                manifest, comparison_config, {case.id: case}, comparison_harness, tmp_path
            ).run()
        )
    assert result.status == "invalid"
    assert result.reason == "identity_mismatch"


def test_discarded_generation_remains_private_evidence(
    comparison_harness: ScenarioHarness,
    comparison_config: ComparisonConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def generated(
        self: ContextModel, *, user_input: str, system_supplied_context: str
    ) -> str:
        del self, user_input, system_supplied_context
        return "unique discarded model output"

    monkeypatch.setattr(ContextModel, "generate", generated)
    case = next(
        c
        for c in load_scenarios(Path("experiments/scenarios.json"))
        if c.id == "revoke-before-release"
    )
    with asyncio.Runner(loop_factory=create_event_loop) as runner:
        pair = runner.run(run_pair(case, comparison_config, comparison_harness))
    context = next(o for o in pair.guarded.observations if o.request is not None)
    assert context.attempt_status == "stale_discarded"
    assert context.body == "unique discarded model output"
    assert all(not o.body for o in pair.guarded.observations if o.surface == "release")


def test_cancelled_allocation_is_removed_after_worker_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started, finish = Event(), Event()
    databases = ExperimentDatabases("postgresql+psycopg://localhost:55466/postgres")
    removed: list[str] = []

    def create(self: ExperimentDatabases, template: str | None) -> tuple[str, str]:
        del template
        started.set()
        assert finish.wait(5)
        self.owned.add("owned-test")
        return "owned-test", "unused"

    def remove(self: ExperimentDatabases, name: str) -> None:
        self.owned.remove(name)
        removed.append(name)

    monkeypatch.setattr(ExperimentDatabases, "create", create)
    monkeypatch.setattr(ExperimentDatabases, "remove", remove)

    async def probe() -> None:
        async def enter() -> None:
            async with databases.database():
                pytest.fail("Cancelled allocation must not enter body")

        task = asyncio.create_task(enter())
        assert await asyncio.to_thread(started.wait, 5)
        _ = task.cancel()
        await asyncio.sleep(0)
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(probe())
    assert not databases.owned
    assert removed == ["owned-test"]


def test_release_failure_retains_generated_text(
    comparison_harness: ScenarioHarness,
    comparison_config: ComparisonConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail(host: HostState, pending: PendingAnswer) -> MessageResponse | None:
        del host, pending
        message = "release failed"
        raise RuntimeError(message)

    monkeypatch.setattr("experiments.host.finish_answer", fail)
    case = load_scenarios(Path("experiments/scenarios.json"))[0]

    async def probe() -> None:
        async with comparison_harness.open_pair(case, comparison_config) as pair:
            with pytest.raises(ExceptionGroup):
                _ = await pair.run_arm(case, comparison_config, "guarded")
            result = pair.arm_records[0].result
            assert result.attempts[0].status == "failed"
            context = next(o for o in result.observations if o.request is not None)
            assert context.body
            assert not any(o.surface == "release" for o in result.observations)

    with asyncio.Runner(loop_factory=create_event_loop) as runner:
        runner.run(probe())


@pytest.mark.parametrize("budget", [400, 5000])
def test_multi_document_truncation_repeats(
    budget: int, comparison_harness: ScenarioHarness, comparison_config: ComparisonConfig
) -> None:
    case = next(
        c for c in load_scenarios(Path("experiments/scenarios.json")) if c.id == "paste-system-only"
    )
    config = comparison_config.model_copy(
        update={"seed": 7, "top_k": 1, "max_context_tokens": budget}
    )
    outcomes: list[list[tuple[str, tuple[str, ...], str]]] = []
    with asyncio.Runner(loop_factory=create_event_loop) as runner:
        for _ in range(3):
            pair = runner.run(run_pair(case, config, comparison_harness))
            documents = {identifier: key for key, identifier in pair.guarded.documents}
            outcomes.append(
                [
                    (o.surface, tuple(documents[r.document_id] for r in o.source_refs), o.body)
                    for o in pair.guarded.observations
                ]
            )
    assert outcomes[0] == outcomes[1] == outcomes[2]


def test_failed_allocation_does_not_remove_unowned_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    databases = ExperimentDatabases("postgresql+psycopg://localhost:55466/postgres")

    def fail(self: ExperimentDatabases, template: str | None) -> tuple[str, str]:
        del self, template
        message = "allocation failed"
        raise RuntimeError(message)

    monkeypatch.setattr(ExperimentDatabases, "create", fail)

    async def probe() -> None:
        with pytest.raises(RuntimeError, match="allocation failed"):
            async with databases.database():
                pytest.fail("Failed allocation must not enter body")

    asyncio.run(probe())
    assert not databases.owned
