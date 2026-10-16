from uuid import UUID

import pytest
from tests.unit.demo_evaluation_fixtures import dataset, reviewed_at

from rag_access_guard_api.demo.evaluation_contracts import (
    DemoCaseResult,
    DemoCaseReview,
    DemoFactCheck,
    DemoSourceRef,
)
from rag_access_guard_api.demo.quality_metrics import summarize_quality


def observations() -> tuple[tuple[DemoCaseResult, ...], tuple[DemoCaseReview, ...]]:
    value = dataset()
    results: list[DemoCaseResult] = []
    reviews: list[DemoCaseReview] = []
    run_id = UUID(int=1000)
    for case in value.cases[20:]:
        evidence = tuple(e for alternative in case.evidence_sets for e in alternative.evidence)
        refs = tuple(
            DemoSourceRef(document_id=e.document_id, version_id=e.version_id, chunk_id=e.chunk_id)
            for e in evidence
        )
        results.append(
            DemoCaseResult(
                run_id=run_id,
                unit_id=case.id,
                case_id=case.id,
                principal_login=case.principal_login,
                request_id=UUID(int=len(results) + 2000),
                status="success",
                elapsed_ms=1000.0,
                phase_timings=None,
                retrieved_refs=refs,
                released_refs=refs,
                answer="факт" if evidence else None,
                model_digest="e" * 64,
                profile_hash=value.profile_hash,
                config_hash="f" * 64,
                policy_revision=1,
                error_code=None,
                gold=case,
                dataset_sha256=value.dataset_sha256,
            )
        )
        reviews.append(
            DemoCaseReview(
                run_id=run_id,
                unit_id=case.id,
                case_id=case.id,
                reviewer="Human test fixture",
                result_sha256=results[-1].computed_hash(),
                reviewed_at=reviewed_at(),
                review_method="human",
                fact_checks=tuple(
                    DemoFactCheck(
                        fact_id="fact",
                        answer_quote="факт",
                        evidence_ids=(e.id,),
                        correct=True,
                        grounded=True,
                    )
                    for e in evidence
                ),
                unsupported_claims=(),
                synthetic_attribution_correct=True,
                abstain_correct=not evidence,
            )
        )
    return tuple(results), tuple(reviews)


@pytest.mark.parametrize(("failures", "expected"), [(3, "pass"), (4, "fail")])
def test_acceptance_requires_twenty_nine_of_thirty_two(failures: int, expected: str) -> None:

    results, reviews = observations()
    changed = tuple(
        row.model_copy(update={"retrieved_refs": (), "released_refs": ()})
        if index < failures
        else row
        for index, row in enumerate(results)
    )
    reviews = tuple(
        review.model_copy(update={"result_sha256": result.computed_hash()})
        for review, result in zip(reviews, changed, strict=True)
    )
    summary = summarize_quality(changed, reviews)
    assert summary.status == expected
    assert summary.answerable_count == 32
    assert summary.sufficient_retrieval_count == 32 - failures
    assert summary.complete_grounded_count == 32 - failures


def test_seven_correct_abstentions_fail() -> None:

    results, reviews = observations()
    reviews = (*reviews[:-1], reviews[-1].model_copy(update={"abstain_correct": False}))
    assert summarize_quality(results, reviews).status == "fail"


def test_synthetic_attribution_is_required() -> None:

    results, reviews = observations()
    changed = tuple(
        row.model_copy(update={"synthetic_attribution_correct": False}) for row in reviews
    )
    summary = summarize_quality(results, changed)
    assert summary.complete_grounded_count == 0
    assert summary.status == "fail"


def test_missing_or_duplicate_review_never_passes() -> None:

    results, reviews = observations()
    assert summarize_quality(results, reviews[:-1]).status == "review_required"
    assert summarize_quality(results, (*reviews, reviews[0])).status == "review_required"


def test_unknown_gold_evidence_cannot_ground_a_fact() -> None:

    results, reviews = observations()
    changed = tuple(
        row.model_copy(
            update={
                "fact_checks": tuple(
                    check.model_copy(update={"evidence_ids": ("invented",)})
                    for check in row.fact_checks
                )
            }
        )
        for row in reviews
    )
    summary = summarize_quality(results, changed)
    assert summary.complete_grounded_count == 0
    assert summary.status != "pass"


def test_timeout_stays_in_main_latency_denominator() -> None:

    results, reviews = observations()
    changed = tuple(
        row.model_copy(
            update={
                "status": "timeout",
                "elapsed_ms": 150000.0,
                "answer": None,
                "retrieved_refs": None,
                "released_refs": None,
                "error_code": "timeout",
            }
        )
        if index < 3
        else row
        for index, row in enumerate(results)
    )
    summary = summarize_quality(changed, reviews)
    assert summary.latency.p95_ms is None
    assert summary.latency.failed
    assert summary.latency.success_only_p95_ms == 1000
    assert summary.failed_count == 3
    assert "Infinity" not in summary.model_dump_json()


def test_technical_reviews_cannot_claim_human_quality_pass() -> None:

    results, reviews = observations()
    technical = tuple(r.model_copy(update={"review_method": "technical"}) for r in reviews)
    summary = summarize_quality(results, technical)
    assert summary.status == "review_required"
    assert "technical_review_only" in summary.review_defects
