"""Select one counted model contract after authorization and before final preparation."""

from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Literal, assert_never

from anyio.to_thread import run_sync

from rag_access_guard import CandidateChunk, PrepareDenied, PriorTurn, SourceRef
from rag_access_guard_api.adapters.model_tokens import ModelTokenCounter, get_model_counter
from rag_access_guard_api.adapters.relevance import assess_relevance
from rag_access_guard_api.schemas.evidence_context import (
    CONTEXT,
    HistoricalEvidence,
    SingleTurnEvidence,
)
from rag_access_guard_api.services.chat_repository import get_owned_thread
from rag_access_guard_api.services.chat_state import Reservation
from rag_access_guard_api.services.errors import ForbiddenError
from rag_access_guard_api.services.model_profiles import ProfileId
from rag_access_guard_api.services.origin_context import HostPrepared, prepare_origin_context
from rag_access_guard_api.services.security import PolicyUnitOfWork


@dataclass(frozen=True, slots=True)
class PlanningInput:
    """Retain the already authorized provenance across unlocked relevance inference."""

    reservation: Reservation
    chunks: tuple[CandidateChunk, ...] = field(repr=False)
    history: tuple[PriorTurn, ...] = field(repr=False)
    prepared: HostPrepared = field(repr=False)
    user_input: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class PlannedGeneration:
    """Bind the final context and exact counter to the same server-selected profile."""

    prepared: HostPrepared = field(repr=False)
    counter: ModelTokenCounter = field(repr=False)
    profile_id: ProfileId


@dataclass(frozen=True, slots=True)
class PlanningDenied:
    """Return no protected body or source names on an abandoned plan."""

    reason: Literal["no_context", "policy_changed"]


def evaluated_inputs(
    request: PlanningInput,
) -> tuple[tuple[CandidateChunk, ...], tuple[PriorTurn, ...]]:
    """Retain exact scored rows; shared provenance does not identify historical pairs."""
    selected = CONTEXT.validate_json(request.prepared.prepared.model_context)
    refs = frozenset(
        SourceRef(document_id=row[0], document_version_id=row[1], chunk_id=row[2])
        for row in selected.chunks
    )
    chunks = tuple(chunk for chunk in request.chunks if chunk.source_ref in refs)
    match selected:
        case SingleTurnEvidence():
            return chunks, ()
        case HistoricalEvidence():
            turn_ids = {turn.turn_id for turn in selected.history}
            return chunks, tuple(turn for turn in request.history if turn.turn_id in turn_ids)
        case _:
            assert_never(selected)


async def plan_generation(
    policy: PolicyUnitOfWork,
    session_token: str,
    request: PlanningInput,
    *,
    config_path: Path,
) -> PlannedGeneration | PlanningDenied:
    """Reauthorize after relevance, never inferring with a different counted prompt."""
    route = await assess_relevance(
        config_path,
        user_input=request.user_input,
        system_supplied_context=request.prepared.model_context,
    )
    match route:
        case "none":
            return PlanningDenied("no_context")
        case "strict":
            profile: ProfileId = "qwen25-extractive-small-demo-v3"
        case "sufficient":
            profile = "qwen25-extractive-small-demo-v2"
        case _:
            assert_never(route)
    counter = await run_sync(partial(get_model_counter, profile))
    chunks, history = evaluated_inputs(request)
    async with policy.protected_read(session_token) as uow:
        _ = await get_owned_thread(uow, request.reservation.thread_id)
        if (
            uow.principal.principal_id != request.reservation.principal_id
            or uow.principal.session_id != request.reservation.session_id
        ):
            raise ForbiddenError
        prepared = await prepare_origin_context(
            uow, chunks, history, counter, user_input=request.user_input
        )
    match prepared:
        case HostPrepared():
            if (
                prepared.prepared.source_refs != request.prepared.prepared.source_refs
                or prepared.origin_binding != request.prepared.origin_binding
            ):
                return PlanningDenied("policy_changed")
            return PlannedGeneration(prepared, counter, profile)
        case PrepareDenied():
            return PlanningDenied("policy_changed")
        case None:
            return PlanningDenied("no_context")
        case _:
            assert_never(prepared)
