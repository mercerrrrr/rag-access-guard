"""Immutable values exchanged with the host's policy adapter."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from rag_access_guard._validation import require_nonnegative, require_sha256, require_tuple

type AccessReason = Literal["allowed", "denied", "invalid_provenance", "policy_unavailable"]
type ReleaseReason = AccessReason | Literal["stale_revision"]
type DenialReason = Literal["denied", "invalid_provenance", "policy_unavailable"]


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceRef:
    """The immutable document/version/chunk identity of one source."""

    document_id: UUID
    document_version_id: UUID
    chunk_id: UUID


@dataclass(frozen=True, slots=True, kw_only=True)
class CandidateChunk:
    """Retrieved text whose identity and content still require authorization."""

    source_ref: SourceRef
    text: str
    content_sha256: str
    token_count: int

    def __post_init__(self) -> None:
        """Validate the declared digest and token count."""
        require_sha256(self.content_sha256)
        require_nonnegative(self.token_count)


@dataclass(frozen=True, slots=True, kw_only=True)
class PriorTurn:
    """A historical pair with the full source set of its answer."""

    turn_id: UUID
    user_input: str
    answer: str
    source_refs: tuple[SourceRef, ...]
    provenance_complete: bool

    def __post_init__(self) -> None:
        """Keep historical provenance immutable."""
        require_tuple(self.source_refs)


@dataclass(frozen=True, slots=True, kw_only=True)
class PolicySnapshot:
    """One host-owned policy view; Guard checks its coverage and consistency."""

    principal_id: UUID
    revision: int
    allowed_refs: tuple[SourceRef, ...]
    denied_refs: tuple[SourceRef, ...]
    provenance_valid: bool
    principal_active: bool
    thread_owned: bool | None
    canonical_chunk_hashes: tuple[tuple[SourceRef, str], ...]

    def __post_init__(self) -> None:
        """Validate structure; Guard validates the policy partition."""
        require_nonnegative(self.revision)
        require_tuple(self.allowed_refs)
        require_tuple(self.denied_refs)
        require_tuple(self.canonical_chunk_hashes)
        for entry in self.canonical_chunk_hashes:
            require_tuple(entry)


@dataclass(frozen=True, slots=True, kw_only=True)
class PreparedContext:
    """Context and complete provenance prepared under one policy revision."""

    model_context: str
    source_refs: tuple[SourceRef, ...]
    policy_revision: int
    fingerprint: str

    def __post_init__(self) -> None:
        """Validate the context's revision and fingerprint."""
        require_tuple(self.source_refs)
        require_nonnegative(self.policy_revision)
        require_sha256(self.fingerprint)


@dataclass(frozen=True, slots=True, kw_only=True)
class AccessDecision:
    """A read decision, without source contents or adapter exception details."""

    allowed: bool
    reason: AccessReason
    policy_revision: int | None

    def __post_init__(self) -> None:
        """Validate an available policy revision."""
        if self.policy_revision is not None:
            require_nonnegative(self.policy_revision)


@dataclass(frozen=True, slots=True, kw_only=True)
class ReleaseDecision:
    """A release result, including policy revision conflicts."""

    allowed: bool
    reason: ReleaseReason
    policy_revision: int | None

    def __post_init__(self) -> None:
        """Validate an available policy revision."""
        if self.policy_revision is not None:
            require_nonnegative(self.policy_revision)


@dataclass(frozen=True, slots=True, kw_only=True)
class PrepareDenied:
    """A structured refusal to construct model context."""

    reason: DenialReason
    policy_revision: int | None

    def __post_init__(self) -> None:
        """Validate an available policy revision."""
        if self.policy_revision is not None:
            require_nonnegative(self.policy_revision)
