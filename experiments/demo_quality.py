"""Closed demo-quality adapter for the existing experiment execution lifecycle."""

import sys
from typing import assert_never
from uuid import NAMESPACE_URL, UUID, uuid5

import anyio
import httpx2
from sqlalchemy.exc import SQLAlchemyError

from experiments.demo_calibration import calibration_report
from experiments.demo_execution import execute_demo
from experiments.demo_inputs import DemoInputs
from experiments.demo_lifecycle import DemoExecution
from experiments.demo_run_contracts import (
    DemoRecord,
    DemoRunEnvelope,
    DemoRunPlan,
    DemoUnit,
    make_unit,
)
from experiments.demo_run_storage import DemoRunStore, interrupted_result
from experiments.run_storage import atomic_write
from experiments.suite_invocation import DemoQualityInvocation
from rag_access_guard_api.demo.calibration_contracts import DemoCalibrationResult
from rag_access_guard_api.demo.errors import DemoOwnershipError
from rag_access_guard_api.demo.evaluation import DemoDatasetError, require_frozen_gold
from rag_access_guard_api.demo.evaluation_contracts import DemoCaseResult
from rag_access_guard_api.demo.quality_metrics import summarize_quality
from rag_access_guard_api.schemas.search import SearchError
from rag_access_guard_api.server import create_event_loop
from rag_access_guard_api.services.model_profiles import get_model_profile


def evaluation_plan(inputs: DemoInputs, invocation: DemoQualityInvocation) -> DemoRunPlan:
    """Freeze all request identities, warmup and three rehearsal rounds before execution."""
    cases = tuple(sorted(inputs.dataset.cases, key=lambda c: c.id))
    demo = tuple(c for c in cases if c.is_demo_question)
    warmup = next(
        c for c in cases if c.split == "calibration" and c.expected_behavior == "answerable"
    )
    match invocation.mode:
        case "validate":
            schedule: tuple[DemoUnit, ...] = ()
        case "calibrate":
            schedule = tuple(
                make_unit(c).model_copy(update={"request_id": None})
                for c in cases
                if c.split == "calibration"
            )
        case "holdout":
            schedule = (
                make_unit(warmup, warmup=True),
                *(make_unit(c) for c in cases if c.split == "holdout"),
            )
        case "rehearsal":
            schedule = (
                make_unit(warmup, warmup=True),
                *(make_unit(c, round_number) for round_number in (1, 2, 3) for c in demo),
            )
        case _:
            assert_never(invocation.mode)
    if invocation.mode != "calibrate":
        schedule = tuple(
            u.model_copy(
                update={
                    "request_id": uuid5(
                        NAMESPACE_URL,
                        str(invocation.output.resolve())
                        + inputs.dataset.dataset_sha256
                        + u.unit_id,
                    )
                }
            )
            for u in schedule
        )
    profile = get_model_profile(inputs.config.model_profile)
    return DemoRunPlan(
        output=invocation.output.resolve(),
        git_sha=inputs.code.sha,
        config_sha256=dict(inputs.hashes)[invocation.config],
        dataset_sha256=inputs.dataset.dataset_sha256,
        profile_hash=inputs.dataset.profile_hash,
        model_digest=profile.manifest.model_digest,
        renderer_identity=profile.renderer_revision,
        corpus_sha256=inputs.dataset.corpus_sha256,
        retrieval_config_hash=inputs.retrieval.config_sha256,
        mode=invocation.mode,
        input_hashes=tuple((str(p.resolve()), digest) for p, digest in inputs.hashes),
        schedule=schedule,
    )


async def validate_without_units(_unit: DemoUnit, _run_id: UUID) -> DemoRecord:
    """A validation schedule is empty; accidental inference scheduling must fail closed."""
    raise DemoDatasetError(code="validate_has_no_execution_units")


def write_demo_summary(
    inputs: DemoInputs, execution: DemoExecution, result: DemoRunEnvelope
) -> None:
    """Collection completion never substitutes for independent human factual acceptance."""
    _, records = DemoRunStore(execution.plan).load()
    match execution.plan.mode:
        case "calibrate":
            if result.status == "completed":
                report = calibration_report(
                    inputs, tuple(r for r in records if isinstance(r, DemoCalibrationResult))
                )
                atomic_write(
                    execution.plan.output / "calibration.json",
                    report.model_dump_json(indent=2).encode(),
                )
                atomic_write(
                    execution.plan.output / "retrieval.json",
                    report.config.model_dump_json(indent=2).encode(),
                )
        case "holdout" | "rehearsal":
            rows = tuple(r for r in records if isinstance(r, DemoCaseResult))
            pending = tuple(
                interrupted_result(execution.plan, result, u).model_copy(
                    update={"status": "not_attempted", "error_code": "not_attempted"}
                )
                for u in execution.plan.schedule[len(records) :]
            )
            measured = (*rows, *(r for r in pending if isinstance(r, DemoCaseResult)))
            summary = summarize_quality(measured, ())
            atomic_write(
                execution.plan.output / "quality.json", summary.model_dump_json(indent=2).encode()
            )
            atomic_write(
                execution.plan.output / "warmup.jsonl",
                b"".join((row.model_dump_json() + "\n").encode() for row in rows if row.warmup),
            )
        case "validate":
            pass
        case _:
            assert_never(execution.plan.mode)


def run_demo_quality(invocation: DemoQualityInvocation) -> int:
    """Local validate is connection/inference-free; execution requires explicit reviewed gold."""
    try:
        inputs = DemoInputs.load(invocation)
        if invocation.mode != "validate":
            require_frozen_gold(inputs.dataset)
            if inputs.code.dirty:
                raise DemoDatasetError(code="clean_code_required")
        execution = DemoExecution(
            evaluation_plan(inputs, invocation),
            validate_without_units,
            resume=invocation.resume,
            comparable=inputs.intact if invocation.mode == "validate" else inputs.unchanged,
        )
        if invocation.mode == "validate":
            result = anyio.run(execution.run, backend_options={"loop_factory": create_event_loop})
            if result.status == "completed":
                result = result.model_copy(update={"status": "validated", "reason": None})
            atomic_write(
                invocation.output / "manifest.json", result.model_dump_json(indent=2).encode()
            )
        else:
            result = anyio.run(
                execute_demo, inputs, execution, backend_options={"loop_factory": create_event_loop}
            )
            write_demo_summary(inputs, execution, result)
        _ = sys.stdout.write(f"Run {result.run_id}: {result.status}\n")
    except (
        OSError,
        ValueError,
        RuntimeError,
        DemoOwnershipError,
        SQLAlchemyError,
        httpx2.HTTPError,
        SearchError,
    ):
        _ = sys.stderr.write(
            "Run rejected: invalid private inputs, identity, runtime or output conflict\n"
        )
        return 2
    else:
        return 0 if result.status in {"validated", "completed", "calibrated"} else 1
