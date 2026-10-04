"""Bind transport bytes to the protected projection used by summary metrics."""

from dataclasses import dataclass, replace
from http import HTTPStatus
from typing import Literal

from pydantic import ValidationError

from experiments.observations import ArmResult, Observation
from experiments.scenario_types import FrozenModel
from rag_access_guard import SourceRef
from rag_access_guard_api.schemas.access import AccessibleDocuments
from rag_access_guard_api.schemas.chat import AvailableTurn, MessageResponse, ThreadDetail
from rag_access_guard_api.schemas.sources import SourceContent


class ErrorEnvelope(FrozenModel):
    """Only closed, non-content error responses are eligible as empty evidence."""

    detail: Literal["Not found", "Unauthorized", "Forbidden", "Service unavailable"]


@dataclass(frozen=True, slots=True)
class HttpProjection:
    """Only protected response fields participate; user questions remain user input."""

    body: str = ""
    titles: tuple[str, ...] = ()
    refs: tuple[SourceRef, ...] = ()
    persisted: str | None = None


def _project(observation: Observation) -> HttpProjection:
    if observation.http_status != HTTPStatus.OK:
        error = ErrorEnvelope.model_validate_json(observation.response_body)
        details = {
            401: "Unauthorized",
            403: "Forbidden",
            404: "Not found",
            503: "Service unavailable",
        }
        if details.get(observation.http_status or 0) != error.detail:
            message = "HTTP status and error envelope disagree"
            raise ValueError(message)
        return HttpProjection()
    if observation.surface == "release":
        turn = MessageResponse.model_validate_json(observation.response_body).turn
        if not isinstance(turn, AvailableTurn):
            if turn.state != "neutral":
                message = "HTTP release is not terminal"
                raise ValueError(message)
            return HttpProjection(persisted="neutral")
        return HttpProjection(
            turn.answer,
            tuple(source.title for source in turn.sources),
            tuple(
                SourceRef(
                    document_id=source.document_id,
                    document_version_id=source.document_version_id,
                    chunk_id=source.chunk_id,
                )
                for source in turn.sources
            ),
            "available",
        )
    if observation.surface == "stored_read":
        detail = ThreadDetail.model_validate_json(observation.response_body)
        visible = tuple(turn for turn in detail.turns if isinstance(turn, AvailableTurn))
        refs = tuple(
            dict.fromkeys(
                SourceRef(
                    document_id=source.document_id,
                    document_version_id=source.document_version_id,
                    chunk_id=source.chunk_id,
                )
                for turn in visible
                for source in turn.sources
            )
        )
        return HttpProjection(
            "\n".join(turn.answer for turn in visible),
            tuple(source.title for turn in visible for source in turn.sources),
            refs,
        )
    if observation.surface == "source_read":
        source = SourceContent.model_validate_json(observation.response_body, extra="forbid")
        return HttpProjection(
            source.text,
            (source.title,),
            (
                SourceRef(
                    document_id=source.document_id,
                    document_version_id=source.document_version_id,
                    chunk_id=source.chunk_id,
                ),
            ),
        )
    listed = AccessibleDocuments.model_validate_json(observation.response_body)
    return HttpProjection(titles=tuple(item.title for item in listed.items))


def validate_http(arm: ArmResult) -> ArmResult:
    """Reject contradictions instead of deriving zero leaks from a stale service projection."""
    verified: list[Observation] = []
    for observation in arm.observations:
        if observation.surface == "model_context":
            verified.append(observation)
            continue
        try:
            projection = _project(observation)
        except ValidationError:
            message = "Invalid captured HTTP schema"
            raise ValueError(message) from None
        titles_match = (
            observation.titles in ((), projection.titles)
            if observation.surface == "release"
            else projection.titles == observation.titles
        )
        if (
            (projection.body, projection.refs) != (observation.body, observation.source_refs)
            or not titles_match
            or (projection.persisted is not None and projection.persisted != observation.persisted)
        ):
            message = "Captured HTTP and protected projection disagree"
            raise ValueError(message)
        verified.append(replace(observation, titles=projection.titles))
    return replace(arm, observations=tuple(verified))
