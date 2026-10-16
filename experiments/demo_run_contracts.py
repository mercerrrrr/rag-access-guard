"""Version-two demo schedule, run identity, and truthful lifecycle envelope."""

from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Final, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import Field, TypeAdapter

from experiments.scenario_types import FrozenModel
from rag_access_guard_api.demo.calibration_contracts import DemoCalibrationResult
from rag_access_guard_api.demo.evaluation_contracts import DemoCaseResult, DemoEvaluationCase
from rag_access_guard_api.schemas.demo_corpus import Sha256


class DemoUnit(FrozenModel):
    """A preselected case occurrence; rehearsal rounds have distinct unit IDs."""

    unit_id: str
    case_id: str
    gold: DemoEvaluationCase
    request_id: UUID | None
    round_number: int = 0
    warmup: bool = False


class DemoRunPlan(FrozenModel):
    """All resume-controlled identities, including absolute destination and schedule."""

    output: Path
    git_sha: str
    config_sha256: Sha256
    dataset_sha256: Sha256
    profile_hash: Sha256
    model_digest: Sha256
    renderer_identity: str
    corpus_sha256: Sha256
    retrieval_config_hash: Sha256
    mode: Literal["validate", "calibrate", "holdout", "rehearsal"]
    input_hashes: tuple[tuple[str, str], ...] = ()
    schedule: tuple[DemoUnit, ...]

    def computed_hash(self) -> str:
        """Hash the complete immutable schedule and external input identities."""
        return sha256(self.model_dump_json().encode()).hexdigest()


class DemoRunEnvelope(FrozenModel):
    """Schema two cannot conceal failed or never-started scheduled units."""

    schema_version: Literal[2] = 2
    suite: Literal["demo-quality"] = "demo-quality"
    run_id: UUID
    status: Literal["running", "completed", "failed", "invalid", "validated", "calibrated"]
    git_sha: str
    plan_sha256: Sha256
    config_sha256: Sha256
    records_sha256: Sha256
    scheduled_count: int
    recorded_count: int
    failed_count: int
    not_attempted_count: int
    model_digest: Sha256
    profile_hash: Sha256
    renderer_identity: str
    corpus_sha256: Sha256
    started_at: datetime
    finished_at: datetime | None = None
    reason: str | None = None


class ActiveUnit(FrozenModel):
    """Durable proof a unit may have been sent, even if no answer was recorded."""

    unit_id: str
    preceding_records_sha256: Sha256


type DemoRecord = Annotated[
    DemoCaseResult | DemoCalibrationResult, Field(discriminator="record_type")
]
RECORD_ADAPTER: Final[TypeAdapter[DemoRecord]] = TypeAdapter(DemoRecord)


def make_unit(case: DemoEvaluationCase, round_number: int = 0, *, warmup: bool = False) -> DemoUnit:
    """Freeze request identity once when constructing the immutable schedule."""
    prefix = "warmup" if warmup else str(round_number)
    return DemoUnit(
        unit_id=f"{prefix}:{case.id}",
        case_id=case.id,
        gold=case,
        request_id=uuid5(NAMESPACE_URL, prefix + ":" + case.model_dump_json()),
        round_number=round_number,
        warmup=warmup,
    )
