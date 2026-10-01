"""Seeded, counterbalanced pair schedules with separately labeled warmups."""

from collections.abc import Sequence
from random import Random
from typing import TYPE_CHECKING

from experiments.run_manifest import TrialSpec

if TYPE_CHECKING:
    from experiments.observations import Arm


def schedule_trials(
    case_ids: Sequence[str], *, seed: int, repetitions: int, warmups: int
) -> tuple[TrialSpec, ...]:
    """Schedule every case without treating repeated cases as new threat classes."""
    if repetitions < 1 or warmups < 0:
        message = "Invalid repetition or warmup counts"
        raise ValueError(message)
    rng = Random(seed)  # noqa: S311 -- deterministic experimental scheduling, not credentials.
    offsets = {case: rng.getrandbits(1) for case in case_ids}
    scheduled: list[TrialSpec] = []
    used: set[int] = set()
    for repetition in range(warmups + repetitions):
        for case in case_ids:
            first = (repetition + offsets[case]) % 2
            trial_seed = rng.getrandbits(63)
            while trial_seed in used or Random(trial_seed).getrandbits(1) != first:  # noqa: S311
                trial_seed = rng.getrandbits(63)
            used.add(trial_seed)
            order: tuple[Arm, Arm] = ("baseline", "guarded") if first else ("guarded", "baseline")
            warmup = repetition < warmups
            scheduled.append(
                TrialSpec(
                    trial_id=f"{case}.{repetition}",
                    case_id=case,
                    repetition=repetition,
                    seed=trial_seed,
                    warmup=warmup,
                    order=order,
                    cache_state="warmup"
                    if warmup
                    else "after_warmup"
                    if warmups
                    else "uncontrolled",
                )
            )
    return tuple(scheduled)
