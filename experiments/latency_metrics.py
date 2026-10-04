"""Descriptive timing samples; logical requests retain all retry costs."""

from dataclasses import dataclass
from math import ceil, isfinite
from statistics import median

from experiments.observations import ArmResult
from experiments.timing_admission import validate_timings


@dataclass(frozen=True, slots=True)
class Timing:
    """Nearest-rank p95 is descriptive, including for small samples."""

    n: int
    median_ms: float | None
    p95_ms: float | None
    percentile_method: str = "nearest_rank"


@dataclass(frozen=True, slots=True)
class LatencySample:
    """Stable action identity supports paired comparisons without mixing attempts."""

    action_id: str
    metric: str
    elapsed_ms: float
    model_called: bool
    attempt_number: int | None = None


def describe(values: tuple[float, ...]) -> Timing:
    """Describe finite samples, including signed paired differences."""
    if any(not isfinite(value) for value in values):
        message = "Non-finite timing sample"
        raise ValueError(message)
    ordered = sorted(values)
    return Timing(
        len(values),
        median(values) if values else None,
        ordered[ceil(0.95 * len(values)) - 1] if values else None,
    )


def arm_samples(arm: ArmResult) -> tuple[LatencySample, ...]:
    """HTTP logical latency includes all attempts; stage totals never replace it."""
    validate_timings(arm)
    samples: list[LatencySample] = []
    for attempt in arm.attempts:
        samples.append(
            LatencySample(
                attempt.action_id,
                "attempt",
                attempt.elapsed_ms,
                attempt.model_called,
                attempt.number,
            )
        )
        samples.extend(
            LatencySample(
                attempt.action_id, f"stage.{stage}", elapsed, attempt.model_called, attempt.number
            )
            for stage, elapsed in attempt.stage_ms
        )
    for observation in arm.observations:
        if observation.surface == "model_context":
            continue
        elapsed = observation.http_elapsed_ms
        if elapsed is None:
            message = "Missing HTTP timing"
            raise ValueError(message)
        called = any(
            attempt.model_called
            for attempt in arm.attempts
            if attempt.action_id == observation.action_id
        )
        metric = "logical_request" if observation.surface == "release" else observation.surface
        samples.append(LatencySample(observation.action_id, metric, elapsed, called))
    if any(not isfinite(sample.elapsed_ms) or sample.elapsed_ms < 0 for sample in samples):
        message = "Invalid nonnegative timing sample"
        raise ValueError(message)
    return tuple(samples)
