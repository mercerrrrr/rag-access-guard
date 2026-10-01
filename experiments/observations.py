"""Immutable evidence from actual experiment boundaries, not expected outcomes."""

from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from experiments.input_boundary import BoundaryObservation, ModelRequestObservation
from rag_access_guard import SourceRef

type Arm = Literal["baseline", "guarded"]
type Surface = Literal["model_context", "release", "stored_read", "source_read", "document_list"]


@dataclass(frozen=True, slots=True, kw_only=True)
class Observation:
    """One observed request or committed/read projection with its current policy oracle."""

    action_id: str
    surface: Surface
    source_refs: tuple[SourceRef, ...]
    forbidden_refs: tuple[SourceRef, ...]
    provenance_valid: bool
    elapsed_ms: float
    http_status: int | None = None
    body: str = field(default="", repr=False)
    titles: tuple[str, ...] = ()
    persisted: Literal["available", "neutral", "unchanged"] = "unchanged"
    request: ModelRequestObservation | None = field(default=None, repr=False)
    markers: tuple[tuple[str, BoundaryObservation], ...] = ()
    cache_control: str = ""
    response_body: str = field(default="", repr=False)
    attempt_number: int | None = None
    attempt_status: Literal["completed", "stale_discarded"] | None = None
    attempt_elapsed_ms: float | None = None
    http_elapsed_ms: float | None = None

    @property
    def violation(self) -> bool:
        """Count unauthorized managed content only when actually sent or exposed."""
        exposed = (
            self.request is not None
            if self.surface == "model_context"
            else bool(self.body or self.titles or self.source_refs)
        )
        return exposed and (not self.provenance_valid or bool(self.forbidden_refs))


@dataclass(frozen=True, slots=True, kw_only=True)
class ArmResult:
    """Completed arm and controlled identities used to validate pair comparability."""

    arm: Arm
    config_hash: str
    corpus_hash: str
    renderer_identity: str
    tokenizer_identity: str
    model_identity: str
    observations: tuple[Observation, ...]
    documents: tuple[tuple[str, UUID], ...]
    commit_order: tuple[str, ...] = ()

    @property
    def system_context_violation(self) -> bool:
        """Aggregate structural request violations independently of output markers."""
        return any(o.violation for o in self.observations if o.surface == "model_context")

    @property
    def forbidden_release_count(self) -> int:
        """Count forbidden outputs on the release surface."""
        return sum(o.violation for o in self.observations if o.surface == "release")


@dataclass(frozen=True, slots=True, kw_only=True)
class PairResult:
    """Both completed arms in their actual seeded execution order."""

    case_id: str
    seed: int
    order: tuple[Arm, Arm]
    baseline: ArmResult
    guarded: ArmResult
    comparison_fingerprint: str
