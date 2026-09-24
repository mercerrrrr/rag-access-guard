import math
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest
from tests.calibration_corpus import synthetic_calibration_dataset
from tests.integration.search_fixtures import configure_search

from rag_access_guard_api.schemas.calibration import EvaluationManifest
from rag_access_guard_api.schemas.retrieval_config import load_retrieval_config
from rag_access_guard_api.schemas.search import InvalidSearchError, RetrievalNotConfiguredError
from rag_access_guard_api.services.calibration import ScoredQuery, score_thresholds


def test_calibration_selects_max_f1_with_smallest_threshold_tie() -> None:
    relevant, negative = UUID(int=1), UUID(int=2)
    rows = (
        ScoredQuery(scores=((relevant, 0.9), (negative, 0.2)), relevant=frozenset({relevant})),
        ScoredQuery(scores=((relevant, 0.1), (negative, 0.3)), relevant=frozenset()),
    )
    result = score_thresholds(rows)
    assert result.chosen.threshold == 0.9
    assert result.chosen.tp == 1
    assert result.chosen.fp == 0
    assert result.chosen.fn == 0
    assert result.chosen.f1 == 1.0


def test_empty_prediction_f1_is_zero_and_tie_uses_smallest_threshold() -> None:
    result = score_thresholds((ScoredQuery(scores=((UUID(int=1), 0.5),), relevant=frozenset()),))
    assert result.chosen.threshold == -1.0
    assert result.chosen.f1 == 0.0


def test_equal_positive_f1_selects_smallest_threshold_with_top_five_limit() -> None:
    positives = tuple(UUID(int=index) for index in range(1, 6))
    rows = (
        ScoredQuery(
            scores=(*((identity, 0.9) for identity in positives), (UUID(int=6), 0.8)),
            relevant=frozenset(positives),
        ),
        ScoredQuery(scores=((UUID(int=6), 0.7),), relevant=frozenset()),
    )
    result = score_thresholds(rows)
    assert result.chosen.threshold == 0.8
    assert result.chosen.f1 == 1.0
    assert result.chosen.tp == 5
    assert result.chosen.fp == 0


@pytest.mark.parametrize("overlap", ["query", "content"])
def test_calibration_gold_is_separate_from_evaluation(overlap: str) -> None:
    dataset = synthetic_calibration_dataset()
    manifest = EvaluationManifest(
        query_ids=(dataset.queries[0].id,) if overlap == "query" else (),
        corpus_hashes=(sha256(dataset.corpus[0].text.encode()).hexdigest(),)
        if overlap == "content"
        else (),
    )
    with pytest.raises(InvalidSearchError):
        dataset.require_disjoint(manifest)


@pytest.mark.parametrize(
    "field",
    [
        "model_id",
        "model_revision",
        "tokenizer_revision",
        "chunker_revision",
        "similarity_revision",
        "config_sha256",
    ],
)
def test_calibration_identity_mismatch_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    path = tmp_path / "config.json"
    configure_search(path, monkeypatch)
    original = load_retrieval_config(path)
    changed = original.model_copy(update={field: "f" * 64})
    _ = path.write_text(changed.model_dump_json(), encoding="utf-8")
    with pytest.raises(RetrievalNotConfiguredError):
        _ = load_retrieval_config(path)


@pytest.mark.parametrize("threshold", [math.nan, math.inf, -1.01, 1.01])
def test_invalid_threshold_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, threshold: float
) -> None:
    path = tmp_path / "config.json"
    configure_search(path, monkeypatch, threshold)
    with pytest.raises(RetrievalNotConfiguredError):
        _ = load_retrieval_config(path)
