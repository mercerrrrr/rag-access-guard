"""Deterministic gold coverage, human review binding, and failure-inclusive latency."""

import math
from collections import Counter
from collections.abc import Mapping
from typing import Final, assert_never

from rag_access_guard_api.demo.evaluation_contracts import (
    DemoCaseResult,
    DemoCaseReview,
    DemoLatency,
    DemoQualitySummary,
    DemoSourceRef,
)

MEDIAN_LIMIT_MS: Final = 30000
P95_LIMIT_MS: Final = 60000
DEMO_COUNT: Final = 10
REHEARSAL_COUNT: Final = 30
HOLDOUT_COUNT: Final = 40
ANSWERABLE_COUNT: Final = 32
ABSTAIN_COUNT: Final = 8
QUALITY_MINIMUM: Final = 29


def _identity(ref: DemoSourceRef) -> tuple[str, str, str]:
    return str(ref.document_id), str(ref.version_id), str(ref.chunk_id)


def sufficient_retrieval(result: DemoCaseResult) -> bool:
    """Cover every fact using an admitted alternative's exact observed top-five refs."""
    if result.status != "success" or result.retrieved_refs is None:
        return False
    refs = {_identity(ref) for ref in result.retrieved_refs[:5]}
    facts = {fact.id for fact in result.gold.required_facts}
    return bool(facts) and any(
        {
            fact
            for evidence in alternative.evidence
            if (str(evidence.document_id), str(evidence.version_id), str(evidence.chunk_id)) in refs
            for fact in evidence.fact_ids
        }
        == facts
        for alternative in result.gold.evidence_sets
    )


def _grounded(result: DemoCaseResult, review: DemoCaseReview) -> bool:
    if (
        result.status != "success"
        or result.answer is None
        or result.released_refs is None
        or review.unsupported_claims
        or not review.synthetic_attribution_correct
    ):
        return False
    facts = {fact.id for fact in result.gold.required_facts}
    if Counter(check.fact_id for check in review.fact_checks) != Counter(facts):
        return False
    evidence = {e.id: e for alternative in result.gold.evidence_sets for e in alternative.evidence}
    released = {_identity(ref) for ref in result.released_refs}
    for check in review.fact_checks:
        if not check.correct or not check.grounded or check.answer_quote not in result.answer:
            return False
        if len(set(check.evidence_ids)) != len(check.evidence_ids):
            return False
        for evidence_id in check.evidence_ids:
            witness = evidence.get(evidence_id)
            if (
                witness is None
                or check.fact_id not in witness.fact_ids
                or (str(witness.document_id), str(witness.version_id), str(witness.chunk_id))
                not in released
            ):
                return False
    return bool(facts)


def _quantile(values: tuple[float, ...], fraction: float) -> float | None:
    if not values:
        return None
    value = sorted(values)[math.ceil(len(values) * fraction) - 1]
    return value if math.isfinite(value) else None


def quality_latency(results: tuple[DemoCaseResult, ...]) -> DemoLatency:
    """Treat every failed or unattempted scheduled question as acceptance infinity."""
    successful = tuple(
        row.elapsed_ms for row in results if row.status == "success" and row.elapsed_ms is not None
    )
    durations = tuple(
        row.elapsed_ms if row.status == "success" and row.elapsed_ms is not None else math.inf
        for row in results
    )
    median, p95 = _quantile(durations, 0.5), _quantile(durations, 0.95)
    return DemoLatency(
        median_ms=median,
        p95_ms=p95,
        failed=median is None or p95 is None or median > MEDIAN_LIMIT_MS or p95 > P95_LIMIT_MS,
        success_only_median_ms=_quantile(successful, 0.5),
        success_only_p95_ms=_quantile(successful, 0.95),
    )


def summarize_quality(
    results: tuple[DemoCaseResult, ...], reviews: tuple[DemoCaseReview, ...]
) -> DemoQualitySummary:
    """Produce no PASS without a complete schedule and bound human rubric."""
    measured = tuple(row for row in results if not row.warmup)
    defects: list[str] = []
    defects.extend(_review_method_defects(reviews))
    keys = [(row.run_id, row.unit_id, row.case_id) for row in measured]
    review_keys = [(row.run_id, row.unit_id, row.case_id) for row in reviews]
    if len(set(keys)) != len(keys):
        defects.append("duplicate_result")
    if len(set(review_keys)) != len(review_keys):
        defects.append("duplicate_review")
    if set(review_keys) != set(keys):
        defects.append("review_coverage")
    by_key = dict(zip(review_keys, reviews, strict=True))
    identity = {
        (
            r.run_id,
            r.dataset_sha256,
            r.profile_hash,
            r.config_hash,
            r.model_digest,
            r.policy_revision,
        )
        for r in measured
    }
    if len(identity) != 1:
        defects.append("run_identity_drift")
    sufficient = grounded = abstain = correct_abstain = answerable = 0
    all_pass: dict[str, bool] = {}
    for key, result in zip(keys, measured, strict=True):
        review = by_key.get(key)
        valid_review = (
            review is not None
            and bool(review.reviewer.strip())
            and (review.result_sha256 == result.computed_hash())
        )
        if not valid_review:
            defects.append(f"unbound_review:{result.unit_id}")
        enough = sufficient_retrieval(result)
        complete = valid_review and review is not None and _grounded(result, review)
        match result.gold.expected_behavior:
            case "answerable":
                answerable += 1
                sufficient += enough
                grounded += complete
                all_pass[result.unit_id] = enough and complete
            case "abstain":
                abstain += 1
                correct = bool(
                    valid_review
                    and review is not None
                    and review.abstain_correct
                    and not review.unsupported_claims
                    and result.status == "success"
                )
                correct_abstain += correct
                all_pass[result.unit_id] = correct
            case _:
                assert_never(result.gold.expected_behavior)
    rehearsal, schedule_valid, rounds_passed = _schedule_status(measured, all_pass)
    schedule_valid = schedule_valid and (
        rehearsal or (answerable == ANSWERABLE_COUNT and abstain == ABSTAIN_COUNT)
    )
    quality_pass = (
        rounds_passed == (1, 2, 3)
        if rehearsal
        else (
            sufficient >= QUALITY_MINIMUM
            and grounded >= QUALITY_MINIMUM
            and correct_abstain == ABSTAIN_COUNT
        )
    )
    if not schedule_valid:
        defects.append("incomplete_or_wrong_schedule")
    latency = quality_latency(measured)
    failed = sum(row.status not in {"success", "not_attempted"} for row in measured)
    status = (
        "review_required"
        if defects
        else ("pass" if quality_pass and not latency.failed and failed == 0 else "fail")
    )
    return DemoQualitySummary(
        status=status,
        scheduled_count=len(measured),
        attempted_count=sum(r.status != "not_attempted" for r in measured),
        not_attempted_count=sum(r.status == "not_attempted" for r in measured),
        failed_count=failed,
        answerable_count=answerable,
        sufficient_retrieval_count=sufficient,
        complete_grounded_count=grounded,
        abstain_count=abstain,
        correct_abstain_count=correct_abstain,
        review_defects=tuple(sorted(set(defects))),
        latency=latency,
        rehearsal_rounds_passed=rounds_passed,
    )


def _review_method_defects(reviews: tuple[DemoCaseReview, ...]) -> tuple[str, ...]:
    return (
        ("technical_review_only",)
        if any(review.review_method == "technical" for review in reviews)
        else ()
    )


def _schedule_status(
    measured: tuple[DemoCaseResult, ...], passed: Mapping[str, bool]
) -> tuple[bool, bool, tuple[int, ...]]:
    rounds = sorted({r.round_number for r in measured})
    rehearsal = rounds == [1, 2, 3]
    gold_valid = all(row.case_id == row.gold.id and row.gold.split == "holdout" for row in measured)
    if rehearsal:
        selection = [tuple(r.case_id for r in measured if r.round_number == n) for n in rounds]
        valid = (
            gold_valid
            and len(measured) == REHEARSAL_COUNT
            and all(len(ids) == len(set(ids)) == DEMO_COUNT for ids in selection)
            and all(set(ids) == set(selection[0]) for ids in selection)
            and all(
                row.gold.is_demo_question and row.gold.expected_behavior == "answerable"
                for row in measured
            )
        )
    else:
        valid = (
            gold_valid
            and len(measured) == HOLDOUT_COUNT
            and len({r.case_id for r in measured}) == HOLDOUT_COUNT
            and all(row.round_number == 0 for row in measured)
        )
    rounds_passed = (
        tuple(
            number
            for number in rounds
            if all(passed[r.unit_id] for r in measured if r.round_number == number)
        )
        if rehearsal and valid
        else ()
    )
    return rehearsal, valid, rounds_passed
