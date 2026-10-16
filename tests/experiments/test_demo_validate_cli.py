import json
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from experiments.demo_inputs import DemoInputs, allowed_snapshot_hash
from experiments.demo_quality import evaluation_plan
from experiments.demo_run_contracts import DemoRunEnvelope
from experiments.run_config import synthetic_retrieval
from experiments.suite_dispatch import DemoQualityInvocation
from tests.helpers.demo_corpus_factory import corpus_fixture
from tests.unit.demo_evaluation_fixtures import dataset

from rag_access_guard_api.demo.access import AccessDefinition, SeedUser
from rag_access_guard_api.demo.contracts import DemoMarker, DemoRuntime
from rag_access_guard_api.demo.private_write import owner_only
from rag_access_guard_api.services.demo_corpus import validate_corpus
from rag_access_guard_api.services.evaluation_identity import profile_hash
from rag_access_guard_api.services.model_profiles import DEMO_PROFILE


def private_inputs(root: Path) -> tuple[Path, Path]:
    manifest = corpus_fixture(root / "corpus")
    corpus_hash = validate_corpus(manifest).manifest_sha256
    access = AccessDefinition(
        schema_version=1,
        users=(
            SeedUser(login="operator", display_name="Operator", is_admin=True),
            *tuple(
                SeedUser(login=login, display_name="Synthetic", is_admin=False)
                for login in ("demo.student", "demo.teacher", "demo.staff")
            ),
        ),
        roles=(),
        memberships=(),
        grants=(),
    )
    access_path = root / "access.json"
    _ = access_path.write_text(access.model_dump_json(), encoding="utf-8")
    marker = root / "owner.json"
    _ = marker.write_text(
        DemoMarker(
            instance_id=UUID(int=1),
            dataset_id="neutral-corpus",
            database_name="rag_access_guard_demo",
            database_role="synthetic-role",
        ).model_dump_json()
    )
    retrieval = root / "retrieval.json"
    _ = retrieval.write_text(synthetic_retrieval(corpus_hash).model_dump_json())
    value = dataset().model_copy(
        update={
            "corpus_sha256": corpus_hash,
            "profile_hash": profile_hash(DEMO_PROFILE),
            "access_matrix_sha256": sha256(access.model_dump_json().encode()).hexdigest(),
        }
    )
    value = value.model_copy(update={"allowed_snapshot_sha256": allowed_snapshot_hash(value)})
    value = value.model_copy(update={"dataset_sha256": value.computed_hash()})
    scenarios = root / "dataset.json"
    _ = scenarios.write_text(value.model_dump_json(), encoding="utf-8")
    payload = {
        "dataset_id": "neutral-corpus",
        "database_url": "postgresql+psycopg://synthetic:unused@127.0.0.1:1/rag_access_guard_demo",
        "corpus_manifest": str(manifest),
        "ownership_manifest": str(marker),
        "credentials_path": str(root / "private" / "credentials.json"),
        "access_definition": str(access_path),
        "model_profile": "qwen3-instruct-demo-v1",
        "retrieval_config": str(retrieval),
        "runtime": DemoRuntime(
            repository_root=Path(__file__).resolve().parents[2],
            state_directory=root / "state",
            log_directory=root / "logs",
            ollama_supervisor_config=root / "ollama.json",
        ).model_dump(mode="json"),
    }
    config = root / "config" / "demo.json"
    config.parent.mkdir()
    owner_only(config.parent, initialize=True)
    _ = config.write_text(json.dumps(payload), encoding="utf-8")
    owner_only(config, initialize=True)
    return config, scenarios


def test_validate_primary_and_wrapper_share_run_without_database_or_inference(
    tmp_path: Path,
) -> None:
    config, scenarios = private_inputs(tmp_path)
    output = tmp_path / "evaluation"
    first = subprocess.run(  # noqa: S603 — Fixed interpreter and owned fixture arguments.
        [
            sys.executable,
            "-m",
            "experiments.run",
            "--suite",
            "demo-quality",
            "--mode",
            "validate",
            "--config",
            str(config),
            "--scenarios",
            str(scenarios),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert first.returncode == 0, first.stderr
    before = (output / "manifest.json").read_text()
    second = subprocess.run(  # noqa: S603 — Fixed interpreter and owned fixture arguments.
        [
            sys.executable,
            "scripts/evaluate_demo.py",
            "--mode",
            "validate",
            "--config",
            str(config),
            "--dataset",
            str(scenarios),
            "--output",
            str(output),
            "--resume",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert second.returncode == 0, second.stderr

    original = DemoRunEnvelope.model_validate_json(before)
    repeated = DemoRunEnvelope.model_validate_json((output / "manifest.json").read_bytes())
    assert original.run_id == repeated.run_id
    assert original.plan_sha256 == repeated.plan_sha256
    assert original.status == repeated.status == "validated"
    assert original.scheduled_count == repeated.scheduled_count == 0
    assert not (tmp_path / "private" / "credentials.json").exists()


def test_warmup_is_calibration_and_measured_holdout_is_fixed(tmp_path: Path) -> None:

    config, scenarios = private_inputs(tmp_path)
    invocation = DemoQualityInvocation(
        config=config, scenarios=scenarios, mode="validate", output=tmp_path / "run"
    )
    inputs = DemoInputs.load(invocation)
    plan = evaluation_plan(inputs, invocation.model_copy(update={"mode": "holdout"}))
    assert plan.schedule[0].warmup
    assert plan.schedule[0].gold.split == "calibration"
    measured = plan.schedule[1:]
    assert len(measured) == 40
    assert plan.schedule[0].case_id not in {u.case_id for u in measured}
    rehearsal = evaluation_plan(inputs, invocation.model_copy(update={"mode": "rehearsal"}))
    assert len(rehearsal.schedule) == 31
    assert len({u.unit_id for u in rehearsal.schedule}) == 31


def test_invalid_retrieval_is_sanitized_before_output(tmp_path: Path) -> None:
    config, scenarios = private_inputs(tmp_path)
    _ = (tmp_path / "retrieval.json").write_text('{"threshold":"synthetic-private-value"}')
    output = tmp_path / "rejected"
    result = subprocess.run(  # noqa: S603 — Fixed interpreter and owned fixture arguments.
        [
            sys.executable,
            "-m",
            "experiments.run",
            "--suite",
            "demo-quality",
            "--mode",
            "validate",
            "--config",
            str(config),
            "--scenarios",
            str(scenarios),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 2
    assert "synthetic-private-value" not in result.stderr
    assert "Traceback" not in result.stderr
    assert not output.exists()
