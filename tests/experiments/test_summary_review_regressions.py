from dataclasses import replace
from pathlib import Path

import pytest
from experiments.latency_metrics import arm_samples
from experiments.metrics import summarize_arm
from experiments.observations import ArmRecord, ArmResult, AttemptEvidence
from experiments.pair_admission import admit_pair
from experiments.summary_aggregation import summarize_run
from experiments.trials import TrialRecord
from tests.experiments.test_summary_aggregation import summary_input
from tests.experiments.test_surface_metrics import arm_with, case_with, expectation, release


def replace_arms(record: TrialRecord, baseline: ArmResult, guarded: ArmResult) -> TrialRecord:
    assert record.pair is not None
    return record.model_copy(
        update={
            "pair": replace(record.pair, baseline=baseline, guarded=guarded),
            "arms": (
                ArmRecord(status="completed", result=baseline),
                ArmRecord(status="completed", result=guarded),
            ),
        }
    )


def test_authorized_release_not_in_denial_denominator() -> None:
    check = expectation("ask").model_copy(update={"forbidden_documents": ("staff",)})
    result = summarize_arm(
        case_with((check,)), arm_with((release("ask"),)), {"public.code": "SYNTHETIC_PUBLIC_61"}
    )
    assert dict(result.rates)["release_violation_rate"].denominator == 0


@pytest.mark.parametrize("body", ["Unauthorized text", ""])
def test_denied_persistence_without_refs_is_violation(body: str) -> None:
    check = expectation("ask", allowed=False)
    observed = replace(release("ask", available=False), body=body, persisted="available")
    result = summarize_arm(case_with((check,)), arm_with((observed,)), {})
    assert dict(result.rates)["release_violation_rate"].numerator == 1


def test_conflicting_http_projection_rejected(tmp_path: Path) -> None:
    manifest, record, case = summary_input(tmp_path)
    assert record.pair is not None
    guarded = replace(
        record.pair.guarded,
        observations=(
            replace(
                record.pair.guarded.observations[0],
                response_body='{"detail":"Not found"}',
            ),
        ),
    )
    damaged = replace_arms(record, record.pair.baseline, guarded)
    with pytest.raises(ValueError, match="HTTP"):
        _ = summarize_run(manifest, (damaged,), (case,), {"public.code": "SYNTHETIC_PUBLIC_61"})


def test_logical_latency_cannot_be_shorter_than_attempts() -> None:
    attempt = AttemptEvidence(
        action_id="ask",
        number=1,
        status="completed",
        elapsed_ms=20,
        stage_ms=(),
        model_called=False,
    )
    observed = replace(
        release("ask"),
        http_elapsed_ms=0.001,
        attempt_number=1,
        attempt_status="completed",
        attempt_elapsed_ms=20,
    )
    with pytest.raises(ValueError, match="timing"):
        _ = arm_samples(replace(arm_with((observed,)), attempts=(attempt,)))


def test_captured_context_without_attempt_is_rejected() -> None:
    context = replace(
        release("ask"),
        surface="model_context",
        attempt_number=1,
        attempt_status="completed",
        attempt_elapsed_ms=10,
    )
    with pytest.raises(ValueError, match="timing"):
        _ = arm_samples(arm_with((context, release("ask"))))


@pytest.mark.parametrize("field", ["model_identity", "tokenizer_identity", "renderer_identity"])
def test_matching_adapter_drift_rejected(tmp_path: Path, field: str) -> None:
    manifest, record, _ = summary_input(tmp_path)
    assert record.pair is not None
    damaged = replace_arms(
        record,
        replace(record.pair.baseline, **{field: "different"}),
        replace(record.pair.guarded, **{field: "different"}),
    )
    with pytest.raises(ValueError, match="adapter"):
        _ = admit_pair(damaged, manifest.identity)


def test_not_run_with_executed_records_rejected(tmp_path: Path) -> None:
    manifest, record, case = summary_input(tmp_path)
    with pytest.raises(ValueError, match="lifecycle"):
        _ = summarize_run(manifest.model_copy(update={"status": "not_run"}), (record,), (case,), {})


def test_distinct_case_and_repetition_counts_visible(tmp_path: Path) -> None:
    manifest, record, case = summary_input(tmp_path)
    result = summarize_run(manifest, (record,), (case,), {"public.code": "SYNTHETIC_PUBLIC_61"})
    assert result.measured_cases == 1
    assert result.repetitions == (("allowed", 1),)
