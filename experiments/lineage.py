"""Known synthetic user-origin labels follow only canonically selected history."""

from uuid import UUID

from pydantic import JsonValue, TypeAdapter

from experiments.boundary_capture import merge_user_origins
from rag_access_guard import PreparedContext, PriorTurn
from rag_access_guard.context import matches_history


def selected_origins(
    prepared: PreparedContext,
    history: tuple[PriorTurn, ...],
    ledger: dict[UUID, frozenset[str]],
) -> frozenset[str]:
    """Read explicit turn identities after exact canonical history validation, not text matching."""
    if not prepared.model_context or not matches_history(prepared, history):
        return frozenset()
    value = TypeAdapter[JsonValue](JsonValue).validate_json(prepared.model_context)
    if not isinstance(value, dict):
        return frozenset()
    entries = value.get("history", [])
    if not isinstance(entries, list):
        return frozenset()
    selected = frozenset(
        UUID(turn_id)
        for entry in entries
        if isinstance(entry, dict) and isinstance(turn_id := entry.get("turn_id"), str)
    )
    return frozenset(marker for turn_id in selected for marker in ledger.get(turn_id, ()))


def request_origins(
    question: str,
    markers: frozenset[str],
    selected: frozenset[str],
) -> frozenset[str]:
    """Preserve user labels without granting any document reference to arbitrary input."""
    return merge_user_origins(
        user_input=question, synthetic_markers=markers, prior_origins=selected
    )
