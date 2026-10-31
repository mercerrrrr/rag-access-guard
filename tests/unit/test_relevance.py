from collections.abc import Generator
from pathlib import Path

import pytest
from tests.unit.test_extractive_model_profile import PASSAGE, context_for

from rag_access_guard_api.adapters import relevance
from rag_access_guard_api.adapters.inference_runtime import InferenceRuntime
from rag_access_guard_api.schemas.generation import GenerationUnavailable
from rag_access_guard_api.schemas.relevance_config import INPUT_BYTE_LIMIT
from rag_access_guard_api.services.evidence_context import EvidencePassage, evidence_passages


@pytest.mark.parametrize(
    ("scores", "expected"),
    [
        ((), "none"),
        ((-5.0, -0.01), "none"),
        ((0.0,), "strict"),
        ((1.999,), "strict"),
        ((2.0,), "sufficient"),
        ((-8.0, 3.2), "sufficient"),
    ],
)
def test_relevance_routes_at_the_fixed_boundaries(
    scores: tuple[float, ...], expected: relevance.EvidenceRoute
) -> None:
    # Given finite complete scores, when classifying them, then use the closed policy.
    assert relevance.classify_relevance(scores) == expected


@pytest.mark.parametrize("score", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_score_cannot_select_a_generation_mode(score: float) -> None:
    # Given a failed numeric boundary, when classifying, then fail closed.
    with pytest.raises(GenerationUnavailable):
        _ = relevance.classify_relevance((score,))


@pytest.fixture
def local_runtime(monkeypatch: pytest.MonkeyPatch) -> Generator[InferenceRuntime]:
    runtime = InferenceRuntime()
    monkeypatch.setattr(relevance, "current_runtime", lambda: runtime)
    try:
        yield runtime
    finally:
        runtime.close()


@pytest.mark.anyio
async def test_managed_context_is_scored_through_physical_admission(
    monkeypatch: pytest.MonkeyPatch, local_runtime: InferenceRuntime, tmp_path: Path
) -> None:
    # Given one prepared managed passage and a bounded scoring boundary.
    calls: list[str] = []

    def score(path: Path, question: str, entries: tuple[EvidencePassage, ...]) -> tuple[float, ...]:
        assert path == tmp_path / "relevance.json"
        assert len(entries) == 1
        calls.append(question)
        return (0.5,)

    monkeypatch.setattr(relevance, "_score", score)
    # When assessing, then use one physical job and preserve the input pair.
    assert (
        await relevance.assess_relevance(
            tmp_path / "relevance.json",
            user_input="QUESTION",
            system_supplied_context=context_for(PASSAGE),
        )
        == "strict"
    )
    assert calls == ["QUESTION"]
    assert local_runtime.accepted_embedding_jobs == 1


@pytest.mark.anyio
async def test_score_cardinality_mismatch_is_not_an_empty_evidence_result(
    monkeypatch: pytest.MonkeyPatch, local_runtime: InferenceRuntime, tmp_path: Path
) -> None:
    # Given a failed scoring result for one managed passage.
    def score(
        _path: Path, _question: str, _entries: tuple[EvidencePassage, ...]
    ) -> tuple[float, ...]:
        return ()

    monkeypatch.setattr(relevance, "_score", score)
    # When assessing, then expose unavailability rather than silently claiming no evidence.
    with pytest.raises(GenerationUnavailable):
        _ = await relevance.assess_relevance(
            tmp_path / "relevance.json",
            user_input="QUESTION",
            system_supplied_context=context_for(PASSAGE),
        )
    assert local_runtime.accepted_embedding_jobs == 1


@pytest.mark.anyio
async def test_oversized_managed_input_never_reaches_the_scoring_job(
    local_runtime: InferenceRuntime, tmp_path: Path
) -> None:
    # Given a parseable managed context beyond this adapter's byte limit.
    context = context_for("x" * INPUT_BYTE_LIMIT)
    assert len(evidence_passages(context)) == 1
    # When assessing, then reject the complete input without truncation or native work.
    with pytest.raises(GenerationUnavailable):
        _ = await relevance.assess_relevance(
            tmp_path / "relevance.json", user_input="QUESTION", system_supplied_context=context
        )
    assert local_runtime.accepted_embedding_jobs == 0
