"""Atomic selection of host-supplied historical pairs."""

from uuid import UUID

from rag_access_guard._policy import valid_provenance
from rag_access_guard.ports import PolicyReader
from rag_access_guard.types import PrepareDenied, PriorTurn


async def filter_history(
    principal_id: UUID,
    prior_turns: tuple[PriorTurn, ...],
    policy_reader: PolicyReader,
    *,
    expected_revision: int,
    max_prior_turns: int = 4,
) -> tuple[PriorTurn, ...] | PrepareDenied:
    """Filter the fixed newest window without refilling it from older turns."""
    chosen: list[PriorTurn] = []
    window = prior_turns[-max_prior_turns:] if max_prior_turns else ()
    for turn in window:
        if (
            not turn.provenance_complete
            or not turn.source_refs
            or not turn.answer.strip()
            or "\x00" in turn.answer
        ):
            continue
        refs = tuple(dict.fromkeys(turn.source_refs))
        try:
            snapshot = await policy_reader.snapshot(principal_id, refs)
        except Exception:  # noqa: BLE001 -- external policy failure must deny the entire preparation.
            return PrepareDenied(reason="policy_unavailable", policy_revision=expected_revision)
        if snapshot.principal_id != principal_id or not snapshot.principal_active:
            return PrepareDenied(reason="denied", policy_revision=snapshot.revision)
        if snapshot.revision != expected_revision:
            return PrepareDenied(reason="policy_unavailable", policy_revision=snapshot.revision)
        if valid_provenance(snapshot, refs) and not snapshot.denied_refs:
            chosen.append(turn)
    return tuple(chosen)
