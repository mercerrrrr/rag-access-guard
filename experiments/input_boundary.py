"""Separate synthetic marker observations from managed-provenance violations."""

from dataclasses import dataclass, field

from rag_access_guard import SourceRef


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelRequestObservation:
    """Captured adapter input; provenance validity comes from canonical evidence."""

    user_input: str = field(repr=False)
    system_supplied_context: str = field(repr=False)
    source_refs: tuple[SourceRef, ...]
    forbidden_system_refs: tuple[SourceRef, ...]
    provenance_valid: bool
    user_origin_markers: frozenset[str]


@dataclass(frozen=True, slots=True, kw_only=True)
class BoundaryObservation:
    """Independent structural decision and literal synthetic-marker observations."""

    system_violation: bool
    user_marker_present: bool
    user_origin_marker: bool
    system_marker_present: bool
    output_marker_present: bool


def observe_boundary(
    request: ModelRequestObservation,
    *,
    output: str,
    marker: str,
) -> BoundaryObservation:
    """Observe a known synthetic marker without inferring its document source."""
    if not marker:
        message = "marker must not be empty"
        raise ValueError(message)
    return BoundaryObservation(
        system_violation=not request.provenance_valid or bool(request.forbidden_system_refs),
        user_marker_present=marker in request.user_input,
        user_origin_marker=marker in request.user_origin_markers,
        system_marker_present=marker in request.system_supplied_context,
        output_marker_present=marker in output,
    )
