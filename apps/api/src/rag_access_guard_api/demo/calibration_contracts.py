"""Typed calibration observations and the private grid/config selection artifact."""

from typing import Literal
from uuid import UUID

from rag_access_guard_api.demo.evaluation_contracts import DemoEvaluationCase
from rag_access_guard_api.schemas.demo_corpus import CorpusValue, Sha256
from rag_access_guard_api.schemas.retrieval_config import RetrievalConfig


class DemoChunkScore(CorpusValue):
    """A visible canonical chunk score, not a guessed semantic label."""

    chunk_id: UUID
    score: float


class DemoCalibrationResult(CorpusValue):
    """One actor-aware calibration unit; no generated answer is invented."""

    record_type: Literal["demo-calibration"] = "demo-calibration"
    run_id: UUID
    unit_id: str
    case_id: str
    round_number: int = 0
    warmup: bool = False
    principal_login: str
    request_id: UUID | None = None
    status: Literal["success", "error", "timeout", "interrupted", "not_attempted"]
    elapsed_ms: float | None
    scores: tuple[DemoChunkScore, ...] | None
    relevant: tuple[UUID, ...]
    model_digest: str | None
    profile_hash: Sha256
    config_hash: Sha256
    policy_revision: int | None
    error_code: str | None
    authentication_state: Literal["reused", "unavailable"] = "unavailable"
    authentication_elapsed_ms: None = None
    gold: DemoEvaluationCase
    dataset_sha256: Sha256


class DemoThresholdScore(CorpusValue):
    """The unchanged score_thresholds grid's sufficient statistics."""

    threshold: float
    tp: int
    fp: int
    fn: int
    f1: float


class DemoCalibrationReport(CorpusValue):
    """Selection remains private and cannot update server/global defaults."""

    dataset_sha256: Sha256
    calibration_cases_sha256: Sha256
    corpus_sha256: Sha256
    config: RetrievalConfig
    chosen: DemoThresholdScore
    grid: tuple[DemoThresholdScore, ...]
