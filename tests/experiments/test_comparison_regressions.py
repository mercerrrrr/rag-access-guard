import asyncio
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from experiments.baseline import prepare_baseline
from experiments.comparison_config import ComparisonConfig
from experiments.harness import ScenarioHarness
from experiments.input_boundary import ModelRequestObservation
from experiments.observations import Observation
from experiments.security_scenarios import load_scenarios
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from rag_access_guard import Guard, PreparedContext, PriorTurn, SourceRef
from rag_access_guard_api.adapters.canonical import CanonicalChunk, canonical_query
from rag_access_guard_api.adapters.llm import FakeTokenCounter
from rag_access_guard_api.adapters.policy import PostgresPolicyReader
from rag_access_guard_api.persistence import DocumentChunk
from rag_access_guard_api.server import create_event_loop
from rag_access_guard_api.services.security import PolicyUnitOfWork


def test_replacement_bytes_must_match_original_manifest(
    comparison_harness: ScenarioHarness,
    comparison_config: ComparisonConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = next(
        c
        for c in load_scenarios(Path("experiments/scenarios.json"))
        if c.id == "replace-active-version"
    )
    read_bytes = Path.read_bytes

    def changed_bytes(path: Path) -> bytes:
        data = read_bytes(path)
        return data + b"\nSYNTHETIC_CHANGED_CORPUS" if path.suffix == ".txt" else data

    async def run() -> None:
        async with comparison_harness.open_pair(case, comparison_config) as pair:
            monkeypatch.setattr(Path, "read_bytes", changed_bytes)
            with pytest.raises(ValueError, match="fixture hash mismatch"):
                _ = await pair.run_arm(case, comparison_config, "baseline")

    with asyncio.Runner(loop_factory=create_event_loop) as runner:
        runner.run(run())
    assert not comparison_harness.databases.owned


@pytest.mark.parametrize("invalid", ["incomplete", "empty", "nul"])
def test_baseline_does_not_refill_invalid_history_window(
    invalid: str,
    comparison_harness: ScenarioHarness,
) -> None:
    config = ComparisonConfig(max_prior_turns=1)
    case = load_scenarios(Path("experiments/scenarios.json"))[0]

    async def run() -> None:
        async with (
            comparison_harness.open_pair(case, config) as pair,
            pair.databases.database(template=pair.template) as (_, url),
        ):
            engine = create_async_engine(url, poolclass=NullPool)
            try:
                async with PolicyUnitOfWork(engine).protected_read(
                    pair.corpus.actors[case.principal_key].token
                ) as uow:
                    row = (
                        (
                            await uow.connection.execute(
                                canonical_query()
                                .where(DocumentChunk.document_id == pair.corpus.documents["public"])
                                .limit(1)
                            )
                        )
                        .mappings()
                        .one()
                    )
                    ref = CanonicalChunk.model_validate(row).candidate().source_ref
                    older = PriorTurn(
                        turn_id=uuid4(),
                        user_input="Earlier question",
                        answer="Earlier answer",
                        source_refs=(ref,),
                        provenance_complete=True,
                    )
                    newer = replace(
                        older,
                        turn_id=uuid4(),
                        provenance_complete=invalid != "incomplete",
                        answer={"incomplete": "New answer", "empty": " ", "nul": "bad\x00"}[
                            invalid
                        ],
                    )
                    history = (older, newer)
                    reader = PostgresPolicyReader(uow)
                    baseline = await prepare_baseline(uow, (), history, config, reader)
                    guarded = await Guard(FakeTokenCounter(), 5000, 1).prepare_context(
                        uow.principal.principal_id, (), history, reader
                    )
                    assert isinstance(baseline, PreparedContext)
                    assert isinstance(guarded, PreparedContext)
                    assert baseline.model_context == guarded.model_context == ""
            finally:
                await engine.dispose()

    with asyncio.Runner(loop_factory=create_event_loop) as runner:
        runner.run(run())


def test_skipped_inference_has_no_context_exposure() -> None:
    ref = SourceRef(document_id=uuid4(), document_version_id=uuid4(), chunk_id=uuid4())
    skipped = Observation(
        action_id="ask",
        surface="model_context",
        source_refs=(ref,),
        forbidden_refs=(ref,),
        provenance_valid=True,
        elapsed_ms=1,
    )
    assert not skipped.violation
    actual = replace(
        skipped,
        request=ModelRequestObservation(
            user_input="question",
            system_supplied_context="closed context",
            source_refs=(ref,),
            forbidden_system_refs=(ref,),
            provenance_valid=True,
            user_origin_markers=frozenset(),
        ),
    )
    assert actual.violation
    assert replace(skipped, surface="release", body="exposed").violation
