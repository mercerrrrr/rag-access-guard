"""Case-balanced context adequacy on the actor-visible calibration top five."""

import math
from fractions import Fraction
from typing import assert_never

from rag_access_guard_api.demo.calibration_contracts import (
    DemoCalibrationResult,
    DemoCaseThresholdScore,
)
from rag_access_guard_api.demo.evaluation import DemoDatasetError
from rag_access_guard_api.schemas.search import MAX_RESULTS


def score_case_thresholds(
    rows: tuple[DemoCalibrationResult, ...],
) -> tuple[DemoCaseThresholdScore, ...]:
    """Credit a complete gold alternative or an empty abstain context, not individual chunks."""
    answerable_count = sum(r.gold.expected_behavior == "answerable" for r in rows)
    abstain_count = len(rows) - answerable_count
    values = {s.score for r in rows for s in r.scores or ()}
    if (
        not answerable_count
        or not abstain_count
        or not values
        or any(not math.isfinite(v) or not -1 <= v <= 1 for v in values)
        or any(
            r.status != "success"
            or r.gold.split != "calibration"
            or r.scores is None
            or len(r.scores) > MAX_RESULTS
            or len({s.chunk_id for s in r.scores}) != len(r.scores)
            for r in rows
        )
    ):
        raise DemoDatasetError(code="invalid_calibration_scores")
    grid: list[DemoCaseThresholdScore] = []
    for threshold in sorted(values | {-1.0, 1.0, math.nextafter(max(values), math.inf)}):
        sufficient = empty = chunks = 0
        for row in rows:
            selected = {s.chunk_id for s in row.scores or () if s.score >= threshold}
            chunks += len(selected)
            match row.gold.expected_behavior:
                case "answerable":
                    sufficient += any(
                        {e.chunk_id for e in alternative.evidence} <= selected
                        for alternative in row.gold.evidence_sets
                    )
                case "abstain":
                    empty += not selected
                case _:
                    assert_never(row.gold.expected_behavior)
        grid.append(
            DemoCaseThresholdScore(
                threshold=threshold,
                answerable_count=answerable_count,
                sufficient_answerable=sufficient,
                abstain_count=abstain_count,
                empty_abstain=empty,
                retrieved_chunks=chunks,
                balanced_adequacy=float(
                    (Fraction(sufficient, answerable_count) + Fraction(empty, abstain_count)) / 2
                ),
            )
        )
    return tuple(grid)


def select_case_threshold(grid: tuple[DemoCaseThresholdScore, ...]) -> DemoCaseThresholdScore:
    """Compare exact group-balanced fractions, then context size and the upper boundary."""
    return max(
        grid,
        key=lambda r: (
            Fraction(r.sufficient_answerable, r.answerable_count)
            + Fraction(r.empty_abstain, r.abstain_count),
            -r.retrieved_chunks,
            r.threshold,
        ),
    )


def select_recall_threshold(grid: tuple[DemoCaseThresholdScore, ...]) -> DemoCaseThresholdScore:
    """Preserve answer evidence; missing facts are judged in the generated answer."""
    return max(
        grid,
        key=lambda row: (
            Fraction(row.sufficient_answerable, row.answerable_count),
            -row.retrieved_chunks,
            row.threshold,
        ),
    )
