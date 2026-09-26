"""Access decisions over host-provided policy snapshots."""

import re
from dataclasses import dataclass, replace
from hashlib import sha256
from uuid import UUID

from rag_access_guard._validation import require_nonnegative
from rag_access_guard.context import matches_canonical, render
from rag_access_guard.fingerprint import fingerprint
from rag_access_guard.ports import PolicyReader, TokenCounter
from rag_access_guard.types import (
    AccessDecision,
    CandidateChunk,
    PolicySnapshot,
    PreparedContext,
    PrepareDenied,
    PriorTurn,
    ReleaseDecision,
    SourceRef,
)


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

    async def prepare_context(
        self,
        principal_id: UUID,
        candidate_chunks: tuple[CandidateChunk, ...],
        prior_turns: tuple[PriorTurn, ...],
        policy_reader: PolicyReader,
    ) -> PreparedContext | PrepareDenied:
        """Prepare single-turn context from canonical allowed chunks within the budget."""
        if prior_turns:
            return PrepareDenied(reason="invalid_provenance", policy_revision=None)
        refs = tuple(dict.fromkeys(c.source_ref for c in candidate_chunks))
        try:
            snapshot = await policy_reader.snapshot(principal_id, refs)
        except Exception:  # noqa: BLE001 -- policy boundary must fail closed without details.
            return PrepareDenied(reason="policy_unavailable", policy_revision=None)
        if snapshot.principal_id != principal_id or not snapshot.principal_active:
            return PrepareDenied(reason="denied", policy_revision=snapshot.revision)
        if not _valid_provenance(snapshot, refs):
            return PrepareDenied(reason="invalid_provenance", policy_revision=snapshot.revision)
        hashes = dict(snapshot.canonical_chunk_hashes)
        chosen: dict[SourceRef, CandidateChunk] = {}
        for chunk in candidate_chunks:
            if chunk.source_ref not in hashes:
                continue
            if sha256(chunk.text.encode("utf-8")).hexdigest() != chunk.content_sha256 or (
                chunk.content_sha256 != hashes[chunk.source_ref]
            ):
                return PrepareDenied(reason="invalid_provenance", policy_revision=snapshot.revision)
            _ = chosen.setdefault(chunk.source_ref, chunk)
        chunks = tuple(chosen.values())
        while chunks and self.token_counter.count(render(chunks)) > self.max_context_tokens:
            chunks = chunks[:-1]
        prepared = PreparedContext(
            model_context=render(chunks) if chunks else "",
            source_refs=tuple(c.source_ref for c in chunks),
            policy_revision=snapshot.revision,
            fingerprint="0" * 64,
        )
        return replace(
            prepared,
            fingerprint=fingerprint(prepared, self.token_counter.identity, self.max_context_tokens),
        )

    async def authorize_release(
        self,
        principal_id: UUID,
        thread_id: UUID,
        prepared: PreparedContext,
        policy_reader: PolicyReader,
    ) -> ReleaseDecision:
        """Recheck ownership, current policy and canonical context at the release gate."""
        if not prepared.source_refs or len(set(prepared.source_refs)) != len(prepared.source_refs):
            return ReleaseDecision(allowed=False, reason="invalid_provenance", policy_revision=None)
        try:
            snapshot = await policy_reader.snapshot(
                principal_id, prepared.source_refs, thread_id=thread_id
            )
        except Exception:  # noqa: BLE001 -- policy boundary must fail closed without details.
            return ReleaseDecision(allowed=False, reason="policy_unavailable", policy_revision=None)
        reason = "allowed"
        if (
            snapshot.principal_id != principal_id
            or not snapshot.principal_active
            or snapshot.thread_owned is not True
        ):
            reason = "denied"
        elif snapshot.revision != prepared.policy_revision:
            reason = "stale_revision"
        elif not _valid_provenance(snapshot, prepared.source_refs):
            reason = "invalid_provenance"
        elif snapshot.denied_refs:
            reason = "denied"
        elif (
            prepared.fingerprint
            != fingerprint(prepared, self.token_counter.identity, self.max_context_tokens)
            or self.token_counter.count(prepared.model_context) > self.max_context_tokens
            or not matches_canonical(prepared, snapshot)
        ):
            reason = "invalid_provenance"
        return ReleaseDecision(
            allowed=reason == "allowed", reason=reason, policy_revision=snapshot.revision
        )

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
