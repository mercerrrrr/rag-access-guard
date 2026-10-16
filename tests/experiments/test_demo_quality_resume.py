import subprocess
import sys
from pathlib import Path
from uuid import UUID

import anyio
import experiments.demo_lifecycle as lifecycle
import httpx2
import pytest
from experiments.demo_lifecycle import DemoExecution
from experiments.demo_run_contracts import DemoRunEnvelope, DemoUnit
from experiments.run_lock import exclusive_run_owner
from sqlalchemy.exc import SQLAlchemyError
from tests.experiments.demo_lifecycle_fixtures import run_plan, successful

from rag_access_guard_api.demo.errors import DemoOwnershipError
from rag_access_guard_api.demo.evaluation import DemoDatasetError
from rag_access_guard_api.demo.evaluation_contracts import DemoCaseResult
from rag_access_guard_api.services.errors import UnauthenticatedError


@pytest.mark.parametrize(
    "field",
    [
        "git_sha",
        "config_sha256",
        "profile_hash",
        "model_digest",
        "renderer_identity",
        "corpus_sha256",
    ],
)
def test_envelope_identity_drift_is_rejected_before_resume(tmp_path: Path, field: str) -> None:
    plan = run_plan(tmp_path)

    async def run() -> None:
        result = await DemoExecution(plan, successful).run()
        replacement = "0" * 40 if field == "git_sha" else "0" * 64
        changed = result.model_copy(update={field: replacement})
        _ = (plan.output / "manifest.json").write_text(changed.model_dump_json())
        with pytest.raises(ValueError, match="resume_identity_drift"):
            _ = await DemoExecution(plan, successful, resume=True).run()

    anyio.run(run)


def test_concurrent_process_cannot_own_active_run(tmp_path: Path) -> None:
    tmp_path.mkdir(exist_ok=True)
    source = """from pathlib import Path; import sys
from experiments.run_lock import exclusive_run_owner
with exclusive_run_owner(Path(sys.argv[1])): print('acquired')
"""
    with exclusive_run_owner(tmp_path):
        result = subprocess.run(  # noqa: S603 — Fixed interpreter and owned fixture arguments.
            [sys.executable, "-c", source, str(tmp_path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    assert result.returncode == 1
    assert "active_run_owner" in result.stderr


@pytest.mark.parametrize(
    "failure",
    [
        DemoDatasetError(code="marker_mismatch"),
        DemoOwnershipError(code="synthetic_ownership_rejection"),
        httpx2.ConnectError("synthetic transport detail"),
        httpx2.HTTPStatusError(
            "synthetic login detail",
            request=httpx2.Request("POST", "http://127.0.0.1/api/auth/login"),
            response=httpx2.Response(401),
        ),
        SQLAlchemyError("synthetic SQL detail"),
        UnauthenticatedError(),
    ],
)
def test_failed_admission_records_unattempted_schedule_without_callback(
    tmp_path: Path,
    failure: DemoDatasetError
    | DemoOwnershipError
    | httpx2.HTTPError
    | SQLAlchemyError
    | UnauthenticatedError,
) -> None:
    # Given an admission rejection before any scheduled question can start.
    plan = run_plan(tmp_path / "admission")
    questions: list[str] = []

    async def reject() -> None:
        raise failure

    async def question(unit: DemoUnit, run_id: UUID) -> DemoCaseResult:
        questions.append(unit.unit_id)
        return await successful(unit, run_id)

    async def run() -> None:
        # When the real lifecycle handles the typed admission failure.
        result = await DemoExecution(plan, question, admission=reject).run()
        persisted = DemoRunEnvelope.model_validate_json(
            (plan.output / "manifest.json").read_bytes()
        )
        # Then the finished artifact is truthful without disclosing the underlying detail.
        assert result.status == "failed"
        assert persisted == result
        assert result.finished_at is not None
        assert result.recorded_count == 0
        assert result.not_attempted_count == len(plan.schedule)
        assert result.reason == "admission_failed"
        assert questions == []

    anyio.run(run)


@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_admission_process_interruption_is_not_normalized(
    tmp_path: Path, interruption: type[KeyboardInterrupt] | type[SystemExit]
) -> None:
    # Given process interruption rather than an ordinary admission rejection.
    plan = run_plan(tmp_path)

    async def reject() -> None:
        raise interruption

    async def run() -> None:
        # When interrupted admission propagates through the lifecycle.
        with pytest.raises(interruption):
            _ = await DemoExecution(plan, successful, admission=reject).run()
        persisted = DemoRunEnvelope.model_validate_json(
            (plan.output / "manifest.json").read_bytes()
        )
        # Then an unknown interrupted admission is not relabelled an ordinary failure.
        assert persisted.status == "running"
        assert persisted.finished_at is None
        assert persisted.recorded_count == 0

    anyio.run(run)


def test_admission_cancellation_is_not_normalized(tmp_path: Path) -> None:
    # Given backend cancellation during admission, before any question.
    plan = run_plan(tmp_path)

    async def reject() -> None:
        raise anyio.get_cancelled_exc_class()

    async def run() -> None:
        # When cancellation crosses the lifecycle boundary.
        with pytest.raises(anyio.get_cancelled_exc_class()):
            _ = await DemoExecution(plan, successful, admission=reject).run()
        persisted = DemoRunEnvelope.model_validate_json(
            (plan.output / "manifest.json").read_bytes()
        )
        # Then cancellation retains its meaning instead of manufacturing ordinary failure.
        assert persisted.status == "running"
        assert persisted.finished_at is None
        assert persisted.recorded_count == 0

    anyio.run(run)


def test_existing_output_is_never_overwritten_without_resume(tmp_path: Path) -> None:
    plan = run_plan(tmp_path)

    async def run() -> None:
        _ = await DemoExecution(plan, successful).run()
        before = (plan.output / "records.jsonl").read_bytes()
        with pytest.raises(FileExistsError):
            _ = await DemoExecution(plan, successful).run()
        assert (plan.output / "records.jsonl").read_bytes() == before

    anyio.run(run)


def test_resume_rejects_public_output_before_owner_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    output = repository / "blocked"
    monkeypatch.setattr(
        lifecycle, "__file__", str(repository / "experiments" / "demo_lifecycle.py")
    )
    plan = run_plan(output)
    plan.output.mkdir(parents=True)

    async def run() -> None:
        with pytest.raises(ValueError, match="public_repository_output"):
            _ = await DemoExecution(plan, successful, resume=True).run()
        assert not (plan.output / "owner.lock").exists()

    anyio.run(run)


def test_terminal_failure_is_immutable_on_resume(tmp_path: Path) -> None:
    plan = run_plan(tmp_path)
    calls: list[str] = []

    async def failed(unit: DemoUnit, run_id: UUID) -> DemoCaseResult:
        calls.append(unit.unit_id)
        row = await successful(unit, run_id)
        return row.model_copy(
            update={
                "status": "error",
                "answer": None,
                "retrieved_refs": None,
                "released_refs": None,
                "error_code": "http_failed",
            }
        )

    async def run() -> None:
        result = await DemoExecution(plan, failed).run()
        before = (plan.output / "records.jsonl").read_bytes()
        repeated = await DemoExecution(plan, failed, resume=True).run()
        assert result.status == repeated.status == "failed"
        assert repeated.failed_count == 2
        assert len(calls) == 2
        assert repeated == result
        assert (plan.output / "records.jsonl").read_bytes() == before

    anyio.run(run)


def test_invalid_actor_session_stops_before_unstarted_units(tmp_path: Path) -> None:
    # Given a revoked/expired actor session reported by the unit boundary.
    plan = run_plan(tmp_path)
    calls: list[str] = []

    async def failed(unit: DemoUnit, run_id: UUID) -> DemoCaseResult:
        calls.append(unit.unit_id)
        row = await successful(unit, run_id)
        return row.model_copy(
            update={
                "status": "error",
                "answer": None,
                "retrieved_refs": None,
                "released_refs": None,
                "model_digest": None,
                "error_code": "actor_session_invalid",
            }
        )

    async def run() -> None:
        # When the common schedule receives that terminal session failure.
        result = await DemoExecution(plan, failed).run()
        # Then it retains unstarted denominator instead of retrying/reacquiring authentication.
        assert result.status == "failed"
        assert result.recorded_count == result.failed_count == 1
        assert result.not_attempted_count == 1
        assert len(calls) == 1

    anyio.run(run)
