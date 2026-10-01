"""Bind an observed model request to the experiment's canonical source evidence."""

from dataclasses import dataclass, replace

from experiments.input_boundary import ModelRequestObservation
from rag_access_guard import PolicySnapshot, PreparedContext, PriorTurn, SourceRef
from rag_access_guard.context import matches_canonical, matches_history


@dataclass(frozen=True, slots=True, kw_only=True)
class CanonicalEvidence:
    """Trusted fixture/database evidence, never inferred from generated output."""

    snapshot: PolicySnapshot
    history: tuple[PriorTurn, ...]
    canonical_chunk_hashes: tuple[tuple[SourceRef, str], ...] | None = None


def _canonical_binding(prepared: PreparedContext, evidence: CanonicalEvidence) -> bool:
    snapshot = evidence.snapshot
    allowed, denied = set(snapshot.allowed_refs), set(snapshot.denied_refs)
    if (
        not snapshot.provenance_valid
        or allowed & denied
        or allowed | denied != set(prepared.source_refs)
    ):
        return False
    if not prepared.source_refs:
        return prepared.model_context == ""
    canonical = (
        snapshot
        if evidence.canonical_chunk_hashes is None
        else replace(snapshot, canonical_chunk_hashes=evidence.canonical_chunk_hashes)
    )
    return matches_canonical(prepared, canonical) and matches_history(prepared, evidence.history)


def capture_request(
    *,
    user_input: str,
    system_supplied_context: str,
    prepared: PreparedContext,
    evidence: CanonicalEvidence,
    user_origin_markers: frozenset[str],
) -> ModelRequestObservation:
    """Capture separate adapter arguments and verify their canonical content binding."""
    snapshot = evidence.snapshot
    allowed = set(snapshot.allowed_refs)
    return ModelRequestObservation(
        user_input=user_input,
        system_supplied_context=system_supplied_context,
        source_refs=prepared.source_refs,
        forbidden_system_refs=tuple(
            ref
            for ref in prepared.source_refs
            if ref not in allowed or not snapshot.principal_active or snapshot.thread_owned is False
        ),
        provenance_valid=(
            system_supplied_context == prepared.model_context
            and _canonical_binding(prepared, evidence)
        ),
        user_origin_markers=user_origin_markers,
    )


def merge_user_origins(
    *,
    user_input: str,
    synthetic_markers: frozenset[str],
    prior_origins: frozenset[str],
) -> frozenset[str]:
    """Carry explicitly observed synthetic user-origin labels across selected history."""
    return (prior_origins & synthetic_markers) | frozenset(
        marker for marker in synthetic_markers if marker in user_input
    )
