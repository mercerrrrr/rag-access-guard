from pathlib import Path

from experiments.demo_calibration import calibration_report
from experiments.demo_calibration_scoring import score_case_thresholds, select_case_threshold
from tests.experiments.test_demo_calibration_selection import scored_rows
from tests.integration.demo_session_fixtures import session_inputs


def test_recall_selection_keeps_answer_evidence_when_abstention_scores_overlap(
    tmp_path: Path,
) -> None:
    inputs = session_inputs(tmp_path, "postgresql://unused")
    rows = tuple(
        row.model_copy(
            update={
                "scores": tuple(
                    score.model_copy(update={"score": 0.85}) for score in row.scores or ()
                )
            }
        )
        if row.gold.expected_behavior == "abstain"
        else row
        for row in scored_rows(inputs)
    )

    report = calibration_report(inputs, rows)

    assert report.chosen.threshold == 0.7
    assert report.chosen.sufficient_answerable == 16
    assert report.chosen.empty_abstain == 0
    assert report.config.scorer_revision == "answerable-recall-top5-v1"
    assert report.schema_version == 3


def test_case_balanced_selector_remains_available_for_old_experiment_reproduction(
    tmp_path: Path,
) -> None:
    inputs = session_inputs(tmp_path, "postgresql://unused")
    rows = tuple(
        row.model_copy(
            update={
                "scores": tuple(
                    score.model_copy(update={"score": 0.85}) for score in row.scores or ()
                )
            }
        )
        if row.gold.expected_behavior == "abstain"
        else row
        for row in scored_rows(inputs)
    )

    chosen = select_case_threshold(score_case_thresholds(rows))

    assert chosen.threshold == 0.9
    assert chosen.sufficient_answerable == 8
    assert chosen.empty_abstain == 4
