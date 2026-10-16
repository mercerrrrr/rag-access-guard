"""Frozen external gold and evidence contracts, independent of experiment code."""

import json
from hashlib import sha256
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from rag_access_guard_api.schemas.demo_corpus import CorpusId, CorpusValue, Sha256

type EvaluationSplit = Literal["calibration", "holdout"]
type ExpectedBehavior = Literal["answerable", "abstain"]
type EvaluationText = Annotated[str, Field(min_length=1)]


class DemoRequiredFact(CorpusValue):
    """Independent gold fact with explicit synthetic provenance."""

    id: CorpusId
    text: EvaluationText
    synthetic: bool


class DemoEvidence(CorpusValue):
    """Exact canonical chunk span and its independently assigned fact links."""

    id: CorpusId
    document_id: UUID
    version_id: UUID
    chunk_id: UUID
    span_start: Annotated[int, Field(ge=0)]
    span_end: Annotated[int, Field(gt=0)]
    supporting_text: EvaluationText
    supporting_sha256: Sha256
    fact_ids: Annotated[tuple[CorpusId, ...], Field(min_length=1)]


class DemoEvidenceSet(CorpusValue):
    """One sufficient alternative; every member is a real canonical witness."""

    id: CorpusId
    evidence: Annotated[tuple[DemoEvidence, ...], Field(min_length=1)]


class DemoEvaluationCase(CorpusValue):
    """A question bound before answers to its actor, split and accessible gold."""

    id: CorpusId
    semantic_family: CorpusId
    split: EvaluationSplit
    audience: Literal["student", "employee"]
    principal_login: EvaluationText
    question: EvaluationText
    expected_behavior: ExpectedBehavior
    required_facts: tuple[DemoRequiredFact, ...]
    evidence_sets: tuple[DemoEvidenceSet, ...]
    forbidden_claims: tuple[EvaluationText, ...]
    allowed_version_ids: tuple[UUID, ...]
    is_demo_question: bool
    abstain_reason: EvaluationText | None = None


class DemoGoldReview(CorpusValue):
    """Explicit source-review provenance; technical freeze supports educational engineering only."""

    reviewer: EvaluationText
    reviewed_at: AwareDatetime
    review_method: Literal["human", "technical"]


class DemoEvaluationActors(CorpusValue):
    """External protocol classes classify cases; they never grant content access."""

    student_login: EvaluationText
    teacher_login: EvaluationText
    staff_login: EvaluationText


class DemoEvaluationDataset(CorpusValue):
    """All scientific identities frozen together before threshold selection."""

    schema_version: Literal[1]
    dataset_id: CorpusId
    revision: CorpusId
    actors: DemoEvaluationActors
    corpus_sha256: Sha256
    dataset_sha256: Sha256
    profile_hash: Sha256
    access_matrix_sha256: Sha256
    policy_revision: Annotated[int, Field(ge=0)]
    allowed_snapshot_sha256: Sha256
    gold_frozen_at: AwareDatetime | None
    gold_review: DemoGoldReview | None
    cases: tuple[DemoEvaluationCase, ...]

    def computed_hash(self) -> str:
        """Canonical hash excludes only itself, including human freeze provenance."""
        raw = self.model_dump(mode="json", exclude={"dataset_sha256"})
        return sha256(
            json.dumps(
                raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
            ).encode()
        ).hexdigest()


class DemoSourceRef(CorpusValue):
    """Source identity copied from an authorized observation, never guessed."""

    document_id: UUID
    version_id: UUID
    chunk_id: UUID


class DemoCaseResult(CorpusValue):
    """Terminal observation; missing HTTP telemetry remains explicitly unknown."""

    record_type: Literal["demo-case"] = "demo-case"
    run_id: UUID
    unit_id: EvaluationText
    case_id: CorpusId
    round_number: Annotated[int, Field(ge=0)] = 0
    warmup: bool = False
    principal_login: str
    request_id: UUID | None
    status: Literal["success", "error", "timeout", "interrupted", "not_attempted"]
    elapsed_ms: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None
    phase_timings: dict[str, Annotated[float, Field(ge=0, allow_inf_nan=False)]] | None
    retrieved_refs: tuple[DemoSourceRef, ...] | None
    released_refs: tuple[DemoSourceRef, ...] | None
    answer: str | None
    model_digest: str | None
    profile_hash: Sha256
    config_hash: Sha256
    policy_revision: int | None
    error_code: str | None
    telemetry_reason: str | None = None
    retrieval_observation: Literal["separate_actor_search", "unavailable"] = "unavailable"
    chat_response_elapsed_ms: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    authentication_elapsed_ms: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    authentication_state: Literal["reused", "unavailable"] = "unavailable"
    gold: DemoEvaluationCase
    dataset_sha256: Sha256

    def computed_hash(self) -> str:
        """Bind human review to every byte of the serialized terminal observation."""
        return sha256(self.model_dump_json().encode()).hexdigest()


class DemoFactCheck(CorpusValue):
    """Human fact judgment tied to a literal answer quote and known evidence IDs."""

    fact_id: CorpusId
    answer_quote: EvaluationText
    evidence_ids: Annotated[tuple[CorpusId, ...], Field(min_length=1)]
    correct: bool
    grounded: bool


class DemoCaseReview(CorpusValue):
    """Human final rubric for one exact run and scheduled observation."""

    run_id: UUID
    unit_id: EvaluationText
    case_id: CorpusId
    result_sha256: Sha256
    reviewer: EvaluationText
    reviewed_at: AwareDatetime
    review_method: Literal["human", "technical"]
    fact_checks: tuple[DemoFactCheck, ...]
    unsupported_claims: tuple[EvaluationText, ...]
    synthetic_attribution_correct: bool
    abstain_correct: bool
    notes: str = ""


class DemoLatency(CorpusValue):
    """Null acceptance quantiles encode infinity without nonstandard JSON floats."""

    median_ms: float | None
    p95_ms: float | None
    failed: bool
    success_only_median_ms: float | None
    success_only_p95_ms: float | None


class DemoQualitySummary(CorpusValue):
    """Deterministic assessment; human review remains a mandatory input."""

    status: Literal["pass", "fail", "review_required"]
    scheduled_count: int
    attempted_count: int
    not_attempted_count: int
    failed_count: int
    answerable_count: int
    sufficient_retrieval_count: int
    complete_grounded_count: int
    abstain_count: int
    correct_abstain_count: int
    review_defects: tuple[str, ...]
    latency: DemoLatency
    rehearsal_rounds_passed: tuple[int, ...] = ()
