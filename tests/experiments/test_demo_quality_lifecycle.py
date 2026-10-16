from pathlib import Path
from uuid import UUID

import anyio
import pytest
from experiments.demo_lifecycle import DemoExecution, DemoRunPlan, DemoUnit
from tests.unit.test_demo_quality_metrics import observations

from rag_access_guard_api.demo.evaluation_contracts import DemoCaseResult


def test_shared_lifecycle_persists_active_before_callback_and_terminal_before_clear(
    tmp_path: Path,
) -> None:

    rows, _ = observations()
    selected = rows[:2]
    plan = DemoRunPlan(
        output=tmp_path / "run",
        git_sha="a" * 40,
        config_sha256="b" * 64,
        dataset_sha256=rows[0].dataset_sha256,
        profile_hash=rows[0].profile_hash,
        model_digest="e" * 64,
        renderer_identity="renderer",
        corpus_sha256="c" * 64,
        retrieval_config_hash="f" * 64,
        mode="holdout",
        schedule=tuple(
            DemoUnit(
                unit_id=r.unit_id,
                case_id=r.case_id,
                gold=r.gold,
                request_id=UUID(int=2000 + selected.index(r)),
            )
            for r in selected
        ),
    )

    async def execute(unit: DemoUnit, run_id: UUID) -> DemoCaseResult:
        active = (plan.output / "active.json").read_text()
        assert unit.unit_id in active
        row = next(r for r in selected if r.unit_id == unit.unit_id)
        return row.model_copy(update={"run_id": run_id})

    async def run() -> None:
        result = await DemoExecution(plan, execute).run()
        assert result.status == "completed"
        assert result.recorded_count == 2
        assert result.not_attempted_count == 0
        assert not (plan.output / "active.json").exists()

    anyio.run(run)


@pytest.mark.parametrize("window", ["inside_unit", "after_terminal"])
def test_resume_never_repeats_started_or_terminal_unit(tmp_path: Path, window: str) -> None:

    rows, _ = observations()
    selected = rows[:2]
    plan = DemoRunPlan(
        output=tmp_path / "run",
        git_sha="a" * 40,
        config_sha256="b" * 64,
        dataset_sha256=rows[0].dataset_sha256,
        profile_hash=rows[0].profile_hash,
        model_digest="e" * 64,
        renderer_identity="renderer",
        corpus_sha256="c" * 64,
        retrieval_config_hash="f" * 64,
        mode="holdout",
        schedule=tuple(
            DemoUnit(
                unit_id=r.unit_id,
                case_id=r.case_id,
                gold=r.gold,
                request_id=UUID(int=2000 + selected.index(r)),
            )
            for r in selected
        ),
    )
    attempts: list[str] = []

    async def execute(unit: DemoUnit, run_id: UUID) -> DemoCaseResult:
        attempts.append(unit.unit_id)
        if len(attempts) == 1 and window == "inside_unit":
            raise KeyboardInterrupt
        return next(r for r in selected if r.unit_id == unit.unit_id).model_copy(
            update={"run_id": run_id}
        )

    def crash(stage: str) -> None:
        if window == "after_terminal" and stage == "terminal_written":
            raise KeyboardInterrupt

    async def run() -> None:
        with pytest.raises(KeyboardInterrupt):
            _ = await DemoExecution(plan, execute, checkpoint_hook=crash).run()
        result = await DemoExecution(plan, execute, resume=True).run()
        assert attempts == [selected[0].unit_id, selected[1].unit_id]
        assert result.recorded_count == 2
        assert result.failed_count == int(window == "inside_unit")
        assert result.status == ("failed" if window == "inside_unit" else "completed")

    anyio.run(run)
