from collections import Counter

import pytest
from experiments.run_schedule import schedule_trials


def test_schedule_is_seeded_balanced_and_preserves_warmups() -> None:
    plan = schedule_trials(("first", "second", "third"), seed=7, repetitions=3, warmups=1)
    assert plan == schedule_trials(("first", "second", "third"), seed=7, repetitions=3, warmups=1)
    assert len(plan) == 12
    assert len({trial.trial_id for trial in plan}) == len(plan)
    for case in ("first", "second", "third"):
        trials = [trial for trial in plan if trial.case_id == case]
        assert sum(trial.warmup for trial in trials) == 1
        assert Counter(trial.order[0] for trial in trials) == {"baseline": 2, "guarded": 2}
        assert len({trial.seed for trial in trials}) == len(trials)
    assert all(trial.warmup for trial in plan[:3])
    assert all(not trial.warmup for trial in plan[3:])


@pytest.mark.parametrize(("repetitions", "warmups"), [(0, 0), (-1, 0), (1, -1)])
def test_schedule_rejects_invalid_counts(repetitions: int, warmups: int) -> None:
    with pytest.raises(ValueError, match="counts"):
        _ = schedule_trials(("case",), seed=7, repetitions=repetitions, warmups=warmups)
