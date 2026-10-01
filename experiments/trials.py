"""One trial record per scheduled pair, including failures and partial evidence."""

import asyncio
from datetime import UTC, datetime
from time import perf_counter
from typing import Literal

from experiments.comparison import run_pair
from experiments.comparison_config import ComparisonConfig
from experiments.harness import ScenarioHarness
from experiments.observations import ArmRecord, ExperimentInterruptedError, PairResult
from experiments.run_manifest import TrialSpec
from experiments.scenario_types import FrozenModel, Scenario


class TrialRecord(FrozenModel):
    """A failed or incomplete pair is never admitted as a completed comparison."""

    trial: TrialSpec
    status: Literal["completed", "failed", "invalid"]
    started_at: datetime
    finished_at: datetime
    elapsed_ms: float
    pair: PairResult | None = None
    arms: tuple[ArmRecord, ...] = ()
    error_code: str | None = None


def interrupted(error: BaseException) -> bool:
    """Nested task-group cancellation must stop the schedule, not become another failed pair."""
    if isinstance(error, BaseExceptionGroup):
        return any(interrupted(child) for child in error.exceptions)
    return isinstance(
        error, (KeyboardInterrupt, asyncio.CancelledError, ExperimentInterruptedError)
    )


async def execute_trial(
    trial: TrialSpec, case: Scenario, config: ComparisonConfig, harness: ScenarioHarness
) -> TrialRecord:
    """Keep actual outcomes without exposing raw exception text or connection details."""
    started = datetime.now(UTC)
    clock = perf_counter()
    arms: list[ArmRecord] = []
    pair = None
    status: Literal["completed", "failed", "invalid"] = "completed"
    code = None
    try:
        pair = await run_pair(
            case, config.model_copy(update={"seed": trial.seed}), harness, records=arms
        )
        if pair.order != trial.order:
            status, code, pair = "invalid", "order_mismatch", None
    except (Exception, BaseExceptionGroup, KeyboardInterrupt, asyncio.CancelledError) as error:  # noqa: BLE001 -- preserve failed trials without raw exception disclosure.
        status = "invalid" if isinstance(error, ValueError) else "failed"
        code = "interrupted" if interrupted(error) else "execution_failed"
        pair = None
    return TrialRecord(
        trial=trial,
        status=status,
        started_at=started,
        finished_at=datetime.now(UTC),
        elapsed_ms=(perf_counter() - clock) * 1000,
        pair=pair,
        arms=tuple(arms),
        error_code=code,
    )
