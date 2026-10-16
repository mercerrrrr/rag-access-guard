from pathlib import Path
from uuid import UUID

from experiments.demo_run_contracts import DemoRunPlan, DemoUnit
from tests.unit.test_demo_quality_metrics import observations

from rag_access_guard_api.demo.evaluation_contracts import DemoCaseResult


def run_plan(root: Path) -> DemoRunPlan:
    rows, _ = observations()
    return DemoRunPlan(
        output=root / "run",
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
                unit_id=row.unit_id,
                case_id=row.case_id,
                gold=row.gold,
                request_id=UUID(int=2000 + index),
            )
            for index, row in enumerate(rows[:2])
        ),
    )


async def successful(unit: DemoUnit, run_id: UUID) -> DemoCaseResult:
    rows, _ = observations()
    return next(row for row in rows if row.case_id == unit.case_id).model_copy(
        update={"run_id": run_id}
    )
