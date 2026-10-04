"""Pair-admitted, stratified summaries of private run evidence."""

from collections.abc import Mapping
from hashlib import sha256

from experiments.http_evidence import validate_http
from experiments.latency_metrics import arm_samples, describe
from experiments.metrics import make_rate, summarize_arm
from experiments.observations import ArmResult
from experiments.pair_admission import admit_pair
from experiments.run_manifest import RunCounts, RunManifest, TrialSpec
from experiments.scenario_types import Scenario
from experiments.summary_types import RateRow, Stratum, Summary, TimingRow
from experiments.trials import TrialRecord

type RateKey = tuple[Stratum, str, str]
type TimingKey = tuple[Stratum, str, str, str]


def _validate_records(manifest: RunManifest, records: tuple[TrialRecord, ...]) -> RunCounts:
    if manifest.status == "not_run" and records:
        message = "Inconsistent not_run lifecycle"
        raise ValueError(message)
    counts = RunCounts(
        scheduled=len(manifest.schedule),
        completed=sum(record.status == "completed" for record in records),
        failed=sum(record.status == "failed" for record in records),
        invalid=sum(record.status == "invalid" for record in records),
        attempts=sum(len(arm.result.attempts) for record in records for arm in record.arms),
    )
    ids = [trial.trial_id for trial in manifest.schedule]
    keys = [
        (trial.case_id, trial.repetition, trial.seed, trial.warmup) for trial in manifest.schedule
    ]
    recorded_counts = manifest.counts
    if manifest.status == "not_run" and not records and recorded_counts == RunCounts():
        recorded_counts = RunCounts(scheduled=len(manifest.schedule))
    if (
        len(set(ids)) != len(ids)
        or len(set(keys)) != len(keys)
        or tuple(record.trial for record in records) != manifest.schedule[: len(records)]
        or len(records) > len(manifest.schedule)
        or counts != recorded_counts
    ):
        message = "Inconsistent scheduled pair identities or counts"
        raise ValueError(message)
    if manifest.status == "completed" and (
        manifest.dirty
        or manifest.finished_at is None
        or not records
        or len(records) != len(manifest.schedule)
        or counts.failed
        or counts.invalid
    ):
        message = "Incomplete comparison pairs"
        raise ValueError(message)
    return counts


def _stratum(case: Scenario, trial: TrialSpec, arm: ArmResult) -> Stratum:
    return Stratum(case.class_name.value, trial.cache_state, arm.model_identity, arm.commit_order)


def _paired_timings(
    case: Scenario,
    trial: TrialSpec,
    left: ArmResult,
    right: ArmResult,
    samples: dict[TimingKey, list[float]],
) -> None:
    left_samples = {
        (sample.action_id, sample.metric): sample
        for sample in arm_samples(left)
        if sample.attempt_number is None
    }
    right_samples = {
        (sample.action_id, sample.metric): sample
        for sample in arm_samples(right)
        if sample.attempt_number is None
    }
    if left_samples.keys() != right_samples.keys():
        message = "Missing paired timing surface"
        raise ValueError(message)
    stratum = Stratum(
        case.class_name.value,
        trial.cache_state,
        left.model_identity,
        ("baseline", *left.commit_order, "guarded", *right.commit_order),
    )
    for key, baseline in left_samples.items():
        guarded = right_samples[key]
        called = f"baseline={baseline.model_called};guarded={guarded.model_called}"
        samples.setdefault((stratum, "guarded_minus_baseline", baseline.metric, called), []).append(
            guarded.elapsed_ms - baseline.elapsed_ms
        )


def _collect_timings(
    stratum: Stratum,
    arm: ArmResult,
    samples: dict[TimingKey, list[float]],
) -> None:
    for sample in arm_samples(arm):
        key = (stratum, arm.arm, sample.metric, str(sample.model_called))
        samples.setdefault(key, []).append(sample.elapsed_ms)


def summarize_run(
    manifest: RunManifest,
    records: tuple[TrialRecord, ...],
    cases: tuple[Scenario, ...],
    rubric: Mapping[str, str],
) -> Summary:
    """Admit complete pairs and the exact scenario oracle before deriving rates."""
    counts = _validate_records(manifest, records)
    rates: dict[RateKey, list[int]] = {}
    timings: dict[TimingKey, list[float]] = {}
    refs = {"baseline": 0, "guarded": 0}
    by_case = {case.id: case for case in cases}
    if len(by_case) != len(cases):
        message = "Duplicate scenario oracle"
        raise ValueError(message)
    if manifest.status == "completed":
        for record in records:
            pair = admit_pair(record, manifest.identity)
            case = by_case.get(record.trial.case_id)
            if (
                case is None
                or sha256(
                    (
                        case.model_dump_json()
                        + pair.baseline.config_hash
                        + pair.baseline.corpus_hash
                    ).encode()
                ).hexdigest()
                != pair.comparison_fingerprint
            ):
                message = "Scenario oracle fingerprint mismatch"
                raise ValueError(message)
            for arm in (pair.baseline, pair.guarded):
                verified = validate_http(arm)
                metrics = summarize_arm(case, verified, rubric)
                _ = arm_samples(arm)
                if record.trial.warmup:
                    continue
                stratum = _stratum(case, record.trial, arm)
                refs[arm.arm] += metrics.forbidden_ref_count
                for name, rate in metrics.rates:
                    total = rates.setdefault((stratum, arm.arm, name), [0, 0])
                    total[0] += rate.numerator
                    total[1] += rate.denominator
                _collect_timings(stratum, arm, timings)
            if not record.trial.warmup:
                _paired_timings(case, record.trial, pair.baseline, pair.guarded, timings)
    repetitions = {
        case_id: sum(
            record.trial.case_id == case_id and not record.trial.warmup for record in records
        )
        for case_id in sorted(
            {record.trial.case_id for record in records if not record.trial.warmup}
        )
    }
    return Summary(
        str(manifest.run_id),
        manifest.identity.git_sha,
        manifest.identity.runtime_config_hash,
        manifest.status,
        manifest.live_status,
        counts,
        len(manifest.schedule) - len(records),
        sum(record.trial.warmup for record in records),
        len(repetitions),
        tuple(repetitions.items()),
        tuple(RateRow(*key, make_rate(*value)) for key, value in rates.items()),
        tuple(TimingRow(*key, describe(tuple(values))) for key, values in timings.items()),
        tuple(refs.items()),
    )
