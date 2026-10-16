from typing import Literal, assert_never
from uuid import UUID

import pytest
from tests.unit.test_demo_quality_metrics import observations

from rag_access_guard_api.demo.evaluation_contracts import DemoCaseResult, DemoCaseReview
from rag_access_guard_api.demo.quality_metrics import summarize_quality


def bound_review(row: DemoCaseResult, template: DemoCaseReview) -> DemoCaseReview:
    return template.model_copy(
        update={
            "unit_id": row.unit_id,
            "case_id": row.case_id,
            "result_sha256": row.computed_hash(),
        }
    )


def rehearsal_observations() -> tuple[tuple[DemoCaseResult, ...], tuple[DemoCaseReview, ...]]:
    source, reviews = observations()
    selected = tuple(row for row in source if row.gold.is_demo_question)
    review_by_case = {review.case_id: review for review in reviews}
    rows = tuple(
        row.model_copy(
            update={
                "unit_id": f"round-{number}:{row.case_id}",
                "round_number": number,
                "request_id": UUID(int=number * 10000 + index),
            }
        )
        for number in (1, 2, 3)
        for index, row in enumerate(selected)
    )
    return rows, tuple(bound_review(row, review_by_case[row.case_id]) for row in rows)


def test_ten_distinct_demo_questions_in_three_rounds_can_pass() -> None:
    # Given the fixed ten properly classified demo gold cases, once per round.
    rows, reviews = rehearsal_observations()
    assert len({row.gold.id for row in rows}) == 10
    assert len({(row.round_number, row.case_id) for row in rows}) == 30
    # When exact human fixture reviews assess all thirty successful observations.
    summary = summarize_quality(rows, reviews)
    # Then all three rounds can pass without altering the holdout thresholds.
    assert summary.status == "pass"
    assert summary.rehearsal_rounds_passed == (1, 2, 3)


def test_repeating_one_case_with_unique_unit_ids_cannot_pass_rehearsal() -> None:
    # Given thirty distinct execution units that repeat only one demo question.
    rows, reviews = rehearsal_observations()
    repeated = tuple(
        rows[0].model_copy(
            update={
                "unit_id": row.unit_id,
                "round_number": row.round_number,
                "request_id": row.request_id,
            }
        )
        for row in rows
    )
    bound = tuple(bound_review(row, reviews[0]) for row in repeated)
    # When every repeated answer has an exact, otherwise valid human fixture review.
    summary = summarize_quality(repeated, bound)
    # Then distinct unit IDs cannot disguise missing rehearsal question coverage.
    assert summary.status == "review_required"
    assert "incomplete_or_wrong_schedule" in summary.review_defects
    assert summary.rehearsal_rounds_passed != (1, 2, 3)


@pytest.mark.parametrize("fault", ["case_id", "split", "demo_flag", "behavior"])
def test_malformed_rehearsal_gold_never_certifies_three_rounds(
    fault: Literal["case_id", "split", "demo_flag", "behavior"],
) -> None:
    # Given the same malformed classification in all rounds, with reviews rebound exactly.
    rows, reviews = rehearsal_observations()
    match fault:
        case "case_id":
            changed = tuple(
                row.model_copy(update={"case_id": f"other-{row.case_id}"}) for row in rows
            )
        case "split":
            changed = tuple(
                row.model_copy(
                    update={"gold": row.gold.model_copy(update={"split": "calibration"})}
                )
                for row in rows
            )
        case "demo_flag":
            changed = tuple(
                row.model_copy(
                    update={"gold": row.gold.model_copy(update={"is_demo_question": False})}
                )
                for row in rows
            )
        case "behavior":
            changed = tuple(
                row.model_copy(
                    update={"gold": row.gold.model_copy(update={"expected_behavior": "abstain"})}
                )
                for row in rows
            )
        case _:
            assert_never(fault)
    bound = tuple(
        bound_review(row, review).model_copy(update={"abstain_correct": True})
        for row, review in zip(changed, reviews, strict=True)
    )
    # When summary receives valid hashes but mismatched gold identity or classification.
    summary = summarize_quality(changed, bound)
    # Then those observations never certify the required demo rehearsal schedule.
    assert summary.status == "review_required"
    assert "incomplete_or_wrong_schedule" in summary.review_defects
    assert summary.rehearsal_rounds_passed != (1, 2, 3)


def test_technical_rehearsal_reviews_remain_non_human_diagnostics() -> None:
    # Given a valid complete rehearsal with explicitly technical rather than human reviews.
    rows, reviews = rehearsal_observations()
    technical = tuple(
        review.model_copy(update={"review_method": "technical"}) for review in reviews
    )
    # When the educational assessment is summarized.
    summary = summarize_quality(rows, technical)
    # Then complete technical diagnostics never claim human quality PASS.
    assert summary.status == "review_required"
    assert "technical_review_only" in summary.review_defects


@pytest.mark.parametrize("fault", ["case_id", "split"])
def test_holdout_assessment_requires_matching_holdout_gold(
    fault: Literal["case_id", "split"],
) -> None:
    # Given a complete holdout whose observations carry mismatched identity or calibration gold.
    rows, reviews = observations()
    match fault:
        case "case_id":
            changed = tuple(
                row.model_copy(update={"case_id": f"other-{row.case_id}"}) for row in rows
            )
        case "split":
            changed = tuple(
                row.model_copy(
                    update={"gold": row.gold.model_copy(update={"split": "calibration"})}
                )
                for row in rows
            )
        case _:
            assert_never(fault)
    bound = tuple(bound_review(row, review) for row, review in zip(changed, reviews, strict=True))
    # When otherwise successful, exactly reviewed rows cross the assessment boundary.
    summary = summarize_quality(changed, bound)
    # Then the wrong gold identity or split cannot claim holdout acceptance.
    assert summary.status == "review_required"
    assert "incomplete_or_wrong_schedule" in summary.review_defects


@pytest.mark.parametrize(
    "rounds",
    [(1,) * 40, tuple(index % 2 for index in range(40))],
    ids=["all_nonzero", "mixed"],
)
def test_holdout_assessment_rejects_nonzero_or_mixed_rounds(rounds: tuple[int, ...]) -> None:
    # Given forty distinct holdout cases whose measured round linkage is invalid.
    rows, reviews = observations()
    changed = tuple(
        row.model_copy(update={"round_number": number})
        for row, number in zip(rows, rounds, strict=True)
    )
    bound = tuple(bound_review(row, review) for row, review in zip(changed, reviews, strict=True))
    # When exact successful reviews cannot repair the malformed holdout schedule.
    summary = summarize_quality(changed, bound)
    # Then only round-zero holdout observations may certify holdout acceptance.
    assert summary.status == "review_required"
    assert "incomplete_or_wrong_schedule" in summary.review_defects
    assert summary.rehearsal_rounds_passed == ()
