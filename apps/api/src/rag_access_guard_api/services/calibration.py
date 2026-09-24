"""Deterministic micro-F1 threshold selection over exact top-five scores."""

import math
from dataclasses import dataclass
from uuid import UUID

from rag_access_guard_api.schemas.search import MAX_RESULTS, InvalidSearchError


@dataclass(frozen=True, slots=True)
class ScoredQuery:
    """All corpus scores and the independently supplied relevant identities."""

    scores: tuple[tuple[UUID, float], ...]
    relevant: frozenset[UUID]


@dataclass(frozen=True, slots=True)
class ThresholdScore:
    """Micro-aggregated counts at one inclusive similarity boundary."""

    threshold: float
    tp: int
    fp: int
    fn: int
    f1: float


@dataclass(frozen=True, slots=True)
class CalibrationScores:
    """The chosen row together with the complete auditable grid."""

    chosen: ThresholdScore
    grid: tuple[ThresholdScore, ...]


def score_thresholds(queries: tuple[ScoredQuery, ...]) -> CalibrationScores:
    """Evaluate a complete threshold grid with stable UUID ties."""
    values = {score for query in queries for _, score in query.scores}
    if (
        not queries
        or not values
        or any(not math.isfinite(value) or not -1 <= value <= 1 for value in values)
    ):
        raise InvalidSearchError
    thresholds = sorted(values | {-1.0, 1.0, math.nextafter(max(values), math.inf)})
    ranked = tuple(
        (tuple(sorted(query.scores, key=lambda item: (-item[1], item[0]))), query.relevant)
        for query in queries
    )
    grid: list[ThresholdScore] = []
    for threshold in thresholds:
        tp = fp = fn = 0
        for scores, relevant in ranked:
            predicted = {identity for identity, value in scores[:MAX_RESULTS] if value >= threshold}
            tp += len(predicted & relevant)
            fp += len(predicted - relevant)
            fn += len(relevant - predicted)
        denominator = 2 * tp + fp + fn
        grid.append(
            ThresholdScore(threshold, tp, fp, fn, 2 * tp / denominator if denominator else 0.0)
        )
    maximum = max(row.f1 for row in grid)
    chosen = next(row for row in grid if math.isclose(row.f1, maximum, rel_tol=0, abs_tol=1e-12))
    return CalibrationScores(chosen=chosen, grid=tuple(grid))
