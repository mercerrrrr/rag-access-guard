from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest
from experiments.observations import ArmRecord, PairResult
from experiments.run_manifest import RunCounts, RunManifest
from experiments.scenario_types import Scenario
from experiments.trials import TrialRecord
from tests.experiments.summary_fixtures import paired_record, persist_run
from tests.experiments.test_surface_metrics import arm_with, case_with, expectation, release

from experiments import summary_aggregation


def summary_input(tmp_path: Path) -> tuple[RunManifest, TrialRecord, Scenario]:
    case = case_with((expectation("ask"),)).model_copy(update={"id": "allowed"})
    arm = arm_with((release("ask"),))
    baseline = replace(arm, arm="baseline")
    fingerprint = sha256(
        (case.model_dump_json() + arm.config_hash + arm.corpus_hash).encode()
    ).hexdigest()
    original = paired_record()
    pair = PairResult(
        case_id=case.id,
        seed=7,
        order=original.trial.order,
        baseline=baseline,
        guarded=arm,
        comparison_fingerprint=fingerprint,
    )
    record = original.model_copy(
        update={
            "pair": pair,
            "arms": (
                ArmRecord(status="completed", result=baseline),
                ArmRecord(status="completed", result=arm),
            ),
        }
    )
    return persist_run(tmp_path, record), record, case


def test_known_answer_summary(tmp_path: Path) -> None:
    manifest, record, case = summary_input(tmp_path)
    result = summary_aggregation.summarize_run(
        manifest, (record,), (case,), {"public.code": "SYNTHETIC_PUBLIC_61"}
    )
    rates = [row for row in result.rates if row.metric == "allowed_utility"]
    assert len(rates) == 2
    assert all(row.rate.value == 1 for row in rates)


def test_partial_pair_rejected(tmp_path: Path) -> None:
    manifest, record, case = summary_input(tmp_path)
    with pytest.raises(ValueError, match="pair"):
        _ = summary_aggregation.summarize_run(
            manifest, (record.model_copy(update={"pair": None}),), (case,), {}
        )


def test_changed_oracle_rejected(tmp_path: Path) -> None:
    manifest, record, case = summary_input(tmp_path)
    case = case.model_copy(update={"user_input": "changed"})
    with pytest.raises(ValueError, match="oracle"):
        _ = summary_aggregation.summarize_run(manifest, (record,), (case,), {})


def test_cold_and_warm_timings_not_pooled(tmp_path: Path) -> None:
    manifest, record, case = summary_input(tmp_path)
    cold = record.model_copy(
        update={"trial": record.trial.model_copy(update={"cache_state": "cold"})}
    )
    warm = record.model_copy(
        update={
            "trial": record.trial.model_copy(
                update={"trial_id": "allowed.1", "repetition": 1, "cache_state": "warm"}
            )
        }
    )
    manifest = manifest.model_copy(
        update={
            "schedule": (cold.trial, warm.trial),
            "counts": RunCounts(scheduled=2, completed=2),
            "identity": manifest.identity.model_copy(
                update={"trial_config_hashes": {"allowed.0": "a" * 64, "allowed.1": "a" * 64}}
            ),
        }
    )
    result = summary_aggregation.summarize_run(
        manifest, (cold, warm), (case,), {"public.code": "SYNTHETIC_PUBLIC_61"}
    )
    rows = [row for row in result.timings if row.metric == "logical_request"]
    assert {row.stratum.cache_state for row in rows} == {"cold", "warm"}
    assert all(row.timing.n == 1 for row in rows)


def test_failed_run_keeps_counts_without_security_rates(tmp_path: Path) -> None:
    manifest, record, case = summary_input(tmp_path)
    failed = record.model_copy(
        update={"status": "failed", "pair": None, "error_code": "execution_failed"}
    )
    manifest = manifest.model_copy(
        update={"status": "failed", "counts": RunCounts(scheduled=1, failed=1)}
    )
    result = summary_aggregation.summarize_run(manifest, (failed,), (case,), {})
    assert result.counts.failed == 1
    assert result.rates == ()


def test_not_run_retains_scheduled_missing_cases(tmp_path: Path) -> None:
    manifest, _, case = summary_input(tmp_path)
    manifest = manifest.model_copy(
        update={"status": "not_run", "counts": RunCounts(), "records_sha256": None}
    )
    result = summary_aggregation.summarize_run(manifest, (), (case,), {})
    assert result.missing == 1
    assert result.counts.scheduled == 1
    assert result.rates == ()
