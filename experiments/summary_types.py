"""Serializable descriptive results without raw prompts or document bodies."""

from dataclasses import dataclass

from experiments.latency_metrics import Timing
from experiments.metrics import Rate
from experiments.run_manifest import RunCounts


@dataclass(frozen=True, slots=True)
class Stratum:
    """Cache, model and observed gate order are never pooled implicitly."""

    case_class: str
    cache_state: str
    model: str
    gate_order: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RateRow:
    """A fraction with its treatment and sampling stratum."""

    stratum: Stratum
    arm: str
    metric: str
    rate: Rate


@dataclass(frozen=True, slots=True)
class TimingRow:
    """A timing distribution preserves model-called and pair-matching strata."""

    stratum: Stratum
    arm: str
    metric: str
    model_called: str
    timing: Timing


@dataclass(frozen=True, slots=True)
class Summary:
    """Run-level lifecycle counts remain visible even when no pair is admissible."""

    run_id: str
    git_sha: str
    config_hash: str
    status: str
    live_status: str
    counts: RunCounts
    missing: int
    warmups: int
    measured_cases: int
    repetitions: tuple[tuple[str, int], ...]
    rates: tuple[RateRow, ...]
    timings: tuple[TimingRow, ...]
    forbidden_ref_count: tuple[tuple[str, int], ...]
