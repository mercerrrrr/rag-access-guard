"""Access decisions over host-provided policy snapshots."""

import re
from dataclasses import dataclass
from uuid import UUID

from rag_access_guard._validation import require_nonnegative
from rag_access_guard.ports import PolicyReader, TokenCounter
from rag_access_guard.types import AccessDecision, PolicySnapshot, SourceRef


def _valid_provenance(snapshot: PolicySnapshot, requested: tuple[SourceRef, ...]) -> bool:
    allowed = set(snapshot.allowed_refs)
    denied = set(snapshot.denied_refs)
    if (
        not snapshot.provenance_valid
        or len(allowed) != len(snapshot.allowed_refs)
        or len(denied) != len(snapshot.denied_refs)
        or allowed & denied
        or allowed | denied != set(requested)
    ):
        return False
    hashes = snapshot.canonical_chunk_hashes
    return (
        len(hashes) == len(allowed)
        and {ref for ref, _ in hashes} == allowed
        and all(re.fullmatch(r"[0-9a-f]{64}", digest) for _, digest in hashes)
    )


@dataclass(frozen=True, slots=True)
class Guard:
    """Keep authorization separate from authentication and content delivery."""

    token_counter: TokenCounter
    max_context_tokens: int = 5000
    max_prior_turns: int = 4

    def __post_init__(self) -> None:
        """Reject negative context limits."""
        require_nonnegative(self.max_context_tokens)
        require_nonnegative(self.max_prior_turns)

    async def authorize_read(
        self, principal_id: UUID, source_refs: tuple[SourceRef, ...], policy_reader: PolicyReader
    ) -> AccessDecision:
        """Decide whether the whole supplied source set may be read."""
        requested = tuple(dict.fromkeys(source_refs))
        if not requested:
            return AccessDecision(allowed=False, reason="invalid_provenance", policy_revision=None)
        try:
            snapshot = await policy_reader.snapshot(principal_id, requested)
        except Exception:  # noqa: BLE001 -- adapter failures must not authorize or expose details.
            return AccessDecision(allowed=False, reason="policy_unavailable", policy_revision=None)
        if snapshot.principal_id != principal_id or not snapshot.principal_active:
            return AccessDecision(allowed=False, reason="denied", policy_revision=snapshot.revision)
        if not _valid_provenance(snapshot, requested):
            return AccessDecision(
                allowed=False, reason="invalid_provenance", policy_revision=snapshot.revision
            )
        allowed = not snapshot.denied_refs
        return AccessDecision(
            allowed=allowed,
            reason="allowed" if allowed else "denied",
            policy_revision=snapshot.revision,
        )
