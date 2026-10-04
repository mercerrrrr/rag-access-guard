from dataclasses import replace

import pytest
from experiments.input_boundary import ModelRequestObservation
from experiments.observations import AttemptEvidence
from tests.experiments.test_surface_metrics import arm_with, release

from experiments import latency_metrics


def test_nearest_rank_percentile_and_empty_sample() -> None:
    result = latency_metrics.describe((10.0, 20.0, 30.0, 40.0))
    assert (result.n, result.median_ms, result.p95_ms) == (4, 25.0, 40.0)
    assert latency_metrics.describe(()).median_ms is None


@pytest.mark.parametrize("attempt_count", [0, 1, 2])
def test_logical_latency_includes_every_attempt(attempt_count: int) -> None:
    attempts = tuple(
        AttemptEvidence(
            action_id="ask",
            number=index + 1,
            status="stale_discarded" if index < attempt_count - 1 else "completed",
            elapsed_ms=10.0,
            stage_ms=(("llm", 6.0),),
            model_called=True,
        )
        for index in range(attempt_count)
    )
    observed = replace(release("ask"), http_elapsed_ms=5.0 + attempt_count * 10.0)
    contexts = tuple(
        replace(
            release("ask"),
            surface="model_context",
            request=ModelRequestObservation(
                user_input="code?",
                system_supplied_context="managed",
                source_refs=release("ask").source_refs,
                forbidden_system_refs=(),
                provenance_valid=True,
                user_origin_markers=frozenset(),
            ),
            attempt_number=attempt.number,
            attempt_status=attempt.status,
            attempt_elapsed_ms=attempt.elapsed_ms,
        )
        for attempt in attempts
    )
    if attempts:
        observed = replace(
            observed,
            attempt_number=attempts[-1].number,
            attempt_status=attempts[-1].status,
            attempt_elapsed_ms=attempts[-1].elapsed_ms,
        )
    arm = replace(arm_with((*contexts, observed)), attempts=attempts)
    samples = latency_metrics.arm_samples(arm)
    logical = next(sample for sample in samples if sample.metric == "logical_request")
    assert logical.elapsed_ms == 5.0 + attempt_count * 10.0
    assert logical.model_called == bool(attempt_count)
    assert (
        sum(sample.elapsed_ms for sample in samples if sample.metric == "attempt")
        == attempt_count * 10.0
    )


@pytest.mark.parametrize("elapsed", [float("nan"), float("inf"), -1.0])
def test_invalid_timing_rejected(elapsed: float) -> None:
    with pytest.raises(ValueError, match="timing"):
        _ = latency_metrics.arm_samples(
            arm_with((replace(release("ask"), http_elapsed_ms=elapsed),))
        )
