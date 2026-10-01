"""Owned mutable scenario evidence and immutable pending release state."""

from dataclasses import dataclass, field
from uuid import UUID

from experiments.comparison_config import ComparisonConfig
from experiments.input_boundary import ModelRequestObservation
from experiments.observations import Arm, AttemptEvidence, Observation
from experiments.ordering import Ordering
from experiments.scenario_types import TamperProvenance
from experiments.seed import Actor, SeededCorpus
from rag_access_guard import PreparedContext, SourceRef
from rag_access_guard_api.schemas.chat import MessageRequest
from rag_access_guard_api.services.chat_state import Reservation
from rag_access_guard_api.services.security import PolicyUnitOfWork


@dataclass(frozen=True, slots=True, kw_only=True)
class PendingAnswer:
    """Unreleased bytes and retry state belong to one exact request and reservation."""

    reservation: Reservation
    prepared: PreparedContext
    body: str = field(repr=False)
    request: MessageRequest
    action_id: str
    started: float
    retry: bool
    captured: ModelRequestObservation | None = field(default=None, repr=False)


@dataclass
class HostState:
    """Accumulate observations and ownership state while executing one isolated arm."""

    policy: PolicyUnitOfWork
    corpus: SeededCorpus
    actor: Actor
    config: ComparisonConfig
    arm: Arm
    synthetic_markers: frozenset[str] = frozenset()
    thread_id: UUID | None = None
    revision: int = 0
    observations: list[Observation] = field(default_factory=list)
    attempts: list[AttemptEvidence] = field(default_factory=list)
    witnesses: dict[str, tuple[SourceRef, ...]] = field(default_factory=dict)
    fault: TamperProvenance | None = None
    policy_failure: bool = False
    ordering: Ordering | None = None
    origins: dict[UUID, frozenset[str]] = field(default_factory=dict)
