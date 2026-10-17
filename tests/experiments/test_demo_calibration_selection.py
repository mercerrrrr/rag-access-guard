from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest
from experiments.demo_calibration import calibration_report
from experiments.demo_calibration_scoring import score_case_thresholds, select_case_threshold
from experiments.demo_inputs import DemoInputs
from tests.integration.demo_session_fixtures import session_inputs

from rag_access_guard_api.demo.calibration_contracts import DemoCalibrationResult, DemoChunkScore
from rag_access_guard_api.demo.evaluation import DemoDatasetError
from rag_access_guard_api.schemas.retrieval_config import load_retrieval_config
from rag_access_guard_api.schemas.search import RetrievalNotConfiguredError


@pytest.fixture(scope="module")
def calibration_inputs(tmp_path_factory: pytest.TempPathFactory) -> DemoInputs:
    return session_inputs(tmp_path_factory.mktemp("calibration"), "postgresql://unused")


def scored_rows(inputs: DemoInputs) -> tuple[DemoCalibrationResult, ...]:
    rows: list[DemoCalibrationResult] = []
    for index, case in enumerate(inputs.dataset.cases[:20]):
        relevant = tuple(e.chunk_id for a in case.evidence_sets for e in a.evidence)
        scores = (
            (DemoChunkScore(chunk_id=relevant[0], score=0.9),)
            if index < 8
            else (
                DemoChunkScore(chunk_id=relevant[0], score=0.7),
                *(DemoChunkScore(chunk_id=UUID(int=n + 100), score=0.8) for n in range(4)),
            )
            if index < 16
            else (DemoChunkScore(chunk_id=UUID(int=200), score=0.65),)
        )
        rows.append(
            DemoCalibrationResult(
                run_id=UUID(int=1000),
                unit_id=case.id,
                case_id=case.id,
                principal_login=case.principal_login,
                status="success",
                elapsed_ms=1.0,
                scores=scores,
                relevant=relevant,
                model_digest="e" * 64,
                profile_hash=inputs.dataset.profile_hash,
                config_hash=inputs.retrieval.config_sha256,
                policy_revision=inputs.dataset.policy_revision,
                error_code=None,
                gold=case,
                dataset_sha256=inputs.dataset.dataset_sha256,
            )
        )
    return tuple(rows)


def test_selection_prefers_complete_case_context_over_chunk_micro_f1(
    calibration_inputs: DemoInputs,
) -> None:
    rows = scored_rows(calibration_inputs)

    report = calibration_report(
        calibration_inputs, rows, objective="case-balanced-adequacy-top5-v1"
    )

    assert report.config.threshold == 0.7
    assert report.config.scorer_revision == "case-balanced-adequacy-top5-v1"


def test_new_config_loads_and_preserves_integrity(
    calibration_inputs: DemoInputs,
    tmp_path: Path,
) -> None:
    report = calibration_report(
        calibration_inputs,
        scored_rows(calibration_inputs),
        objective="case-balanced-adequacy-top5-v1",
    )
    path = tmp_path / "config.json"
    _ = path.write_text(report.config.model_dump_json(), encoding="utf-8")

    loaded = load_retrieval_config(path)

    assert loaded.scorer_revision == "case-balanced-adequacy-top5-v1"
    assert loaded.config_sha256 == report.config.computed_hash()


@pytest.mark.parametrize("mode", ["duplicate", "holdout", "error", "gold_drift"])
def test_selection_rejects_incomplete_or_noncalibration_inputs(
    calibration_inputs: DemoInputs,
    mode: str,
) -> None:
    rows = scored_rows(calibration_inputs)
    changed = {
        "duplicate": rows[1],
        "holdout": rows[0].model_copy(update={"gold": calibration_inputs.dataset.cases[20]}),
        "error": rows[0].model_copy(update={"status": "error"}),
        "gold_drift": rows[0].model_copy(
            update={"gold": rows[0].gold.model_copy(update={"question": "other question"})}
        ),
    }[mode]

    with pytest.raises(DemoDatasetError):
        _ = calibration_report(calibration_inputs, (changed, *rows[1:]))


def test_holdout_changes_do_not_affect_threshold(calibration_inputs: DemoInputs) -> None:
    rows = scored_rows(calibration_inputs)
    changed = replace(
        calibration_inputs,
        dataset=calibration_inputs.dataset.model_copy(
            update={
                "cases": (
                    *calibration_inputs.dataset.cases[:20],
                    *(
                        c.model_copy(update={"question": "changed holdout"})
                        for c in calibration_inputs.dataset.cases[20:]
                    ),
                )
            }
        ),
    )

    report = calibration_report(changed, rows)

    assert report.config.threshold == 0.7


def test_adequacy_requires_one_complete_alternative(calibration_inputs: DemoInputs) -> None:
    rows = scored_rows(calibration_inputs)
    case = rows[0].gold
    original = case.evidence_sets[0]
    evidence = original.evidence[0]
    changed = case.model_copy(
        update={
            "evidence_sets": (
                original.model_copy(
                    update={
                        "evidence": (
                            evidence,
                            evidence.model_copy(update={"chunk_id": UUID(int=500)}),
                        )
                    }
                ),
                original.model_copy(
                    update={
                        "id": "alternative",
                        "evidence": (evidence.model_copy(update={"chunk_id": UUID(int=501)}),),
                    }
                ),
            )
        }
    )
    row = rows[0].model_copy(
        update={
            "gold": changed,
            "scores": (
                DemoChunkScore(chunk_id=evidence.chunk_id, score=0.9),
                DemoChunkScore(chunk_id=UUID(int=500), score=0.6),
                DemoChunkScore(chunk_id=UUID(int=501), score=0.7),
            ),
        }
    )

    grid = score_case_thresholds((row, *rows[1:]))

    assert next(r for r in grid if r.threshold == 0.9).sufficient_answerable == 7
    assert next(r for r in grid if r.threshold == 0.7).sufficient_answerable == 16


def test_answerable_and_abstain_groups_have_equal_weight(calibration_inputs: DemoInputs) -> None:
    rows = scored_rows(calibration_inputs)
    changed = rows[16].model_copy(
        update={"scores": (DemoChunkScore(chunk_id=UUID(int=200), score=0.75),)}
    )

    chosen = select_case_threshold(score_case_thresholds((*rows[:16], changed, *rows[17:])))

    assert chosen.threshold == 0.7
    assert (chosen.sufficient_answerable, chosen.empty_abstain) == (16, 3)
    assert chosen.balanced_adequacy == 0.875


@pytest.mark.parametrize("extra_context", [True, False])
def test_ties_prefer_fewer_chunks_then_higher_threshold(
    calibration_inputs: DemoInputs,
    *,
    extra_context: bool,
) -> None:
    rows = tuple(
        r.model_copy(
            update={
                "scores": (
                    DemoChunkScore(chunk_id=r.relevant[0], score=0.9),
                    *(
                        (DemoChunkScore(chunk_id=UUID(int=500), score=0.2),)
                        if extra_context
                        else ()
                    ),
                )
            }
        )
        if r.relevant
        else r.model_copy(update={"scores": ()})
        for r in scored_rows(calibration_inputs)
    )

    grid = score_case_thresholds(rows)
    chosen = select_case_threshold(grid)

    assert chosen.threshold == 0.9
    assert chosen.retrieved_chunks == 16
    tied = tuple(r for r in grid if r.balanced_adequacy == 1.0)
    assert len(tied) >= 2
    assert {r.retrieved_chunks for r in tied} == ({16, 32} if extra_context else {16})


@pytest.mark.parametrize("mode", ["nan", "infinity", "range", "duplicate", "six", "missing"])
def test_malformed_scores_are_rejected(calibration_inputs: DemoInputs, mode: str) -> None:
    rows = scored_rows(calibration_inputs)
    score = DemoChunkScore(chunk_id=UUID(int=1), score=0.9)
    changed = {
        "scores": {
            "nan": (score.model_copy(update={"score": float("nan")}),),
            "infinity": (score.model_copy(update={"score": float("inf")}),),
            "range": (score.model_copy(update={"score": 1.1}),),
            "duplicate": (score, score),
            "six": tuple(score.model_copy(update={"chunk_id": UUID(int=n)}) for n in range(6)),
            "missing": None,
        }[mode]
    }

    with pytest.raises(DemoDatasetError):
        _ = score_case_thresholds((rows[0].model_copy(update=changed), *rows[1:]))


def test_grid_is_independent_of_row_order(calibration_inputs: DemoInputs) -> None:
    rows = scored_rows(calibration_inputs)

    grid = score_case_thresholds(tuple(reversed(rows)))

    assert grid == score_case_thresholds(rows)


@pytest.mark.parametrize("mode", ["unknown_revision", "unhashed_revision"])
def test_revision_cannot_bypass_config_integrity(
    calibration_inputs: DemoInputs,
    tmp_path: Path,
    mode: str,
) -> None:
    config = calibration_report(calibration_inputs, scored_rows(calibration_inputs)).config
    changed = config.model_copy(
        update={
            "scorer_revision": ("unknown" if mode == "unknown_revision" else "micro-f1-top5-v1")
        }
    )
    if mode == "unknown_revision":
        changed = changed.model_copy(update={"config_sha256": changed.computed_hash()})
    path = tmp_path / "config.json"
    _ = path.write_text(changed.model_dump_json(), encoding="utf-8")

    with pytest.raises(RetrievalNotConfiguredError):
        _ = load_retrieval_config(path)
