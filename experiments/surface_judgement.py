"""Evaluate managed provenance separately from literal marker observations."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

    from rag_access_guard import SourceRef

from experiments.observations import ArmResult, Observation
from experiments.scenario_types import SurfaceExpectation
from rag_access_guard_api.schemas.access import AccessibleDocuments


@dataclass(frozen=True, slots=True)
class SurfaceJudgement:
    """One logical surface, including every discarded model attempt."""

    violation: bool
    conforms: bool
    forbidden_ref_count: int


def judge_surface(
    expected: SurfaceExpectation,
    observations: tuple[Observation, ...],
    arm: ArmResult,
) -> SurfaceJudgement:
    """Use the scenario oracle and captured policy evidence, never guessed text origins."""
    documents = dict(arm.documents)
    forbidden = {documents[key] for key in expected.forbidden_documents}
    required = {documents[key] for key in expected.documents}
    exposed_ids: set[UUID] = set()
    forbidden_refs: set[SourceRef] = set()
    violation = False
    for observation in observations:
        if expected.surface == "document_list":
            listed = AccessibleDocuments.model_validate_json(observation.response_body)
            listed_ids = {document.id for document in listed.items}
            exposed_ids.update(listed_ids)
            violation |= bool(listed_ids & forbidden)
            continue
        request = observation.request
        refs = request.source_refs if request is not None else observation.source_refs
        exposed = (
            request is not None
            if expected.surface == "model_context"
            else bool(observation.body or observation.titles or refs)
        )
        if expected.surface == "model_context" and request is None:
            continue
        exposed_ids.update(ref.document_id for ref in refs)
        denied = set(observation.forbidden_refs)
        denied.update(ref for ref in refs if ref.document_id in forbidden)
        if request is not None:
            denied.update(request.forbidden_system_refs)
        forbidden_refs.update(denied)
        valid = observation.provenance_valid and (request is None or request.provenance_valid)
        violation |= exposed and (not valid or bool(denied))
        if expected.surface != "model_context" and expected.authorization != "allow":
            violation |= exposed or observation.persisted == "available"
    last = observations[-1]
    conforms = not violation and exposed_ids == required
    if expected.surface == "model_context":
        if expected.llm_calls is not None:
            conforms &= sum(item.request is not None for item in observations) == expected.llm_calls
    else:
        conforms &= (
            last.http_status == expected.http_status and last.persisted == expected.persisted
        )
        directives = {value.strip().lower() for value in last.cache_control.split(",")}
        conforms &= {"private", "no-store"}.issubset(directives)
        if expected.body in {"redacted", "neutral"}:
            conforms &= not (last.body or last.titles or last.source_refs)
        elif expected.body == "protected":
            conforms &= bool(last.body)
    return SurfaceJudgement(violation, conforms, len(forbidden_refs))
