"""Reauthorize each stored answer before loading any protected response fields."""

from typing import assert_never
from uuid import UUID

from sqlalchemy import select

from rag_access_guard import Guard, SourceRef
from rag_access_guard_api.adapters.llm import FakeTokenCounter
from rag_access_guard_api.adapters.policy import PostgresPolicyReader
from rag_access_guard_api.persistence import ChatTurn, Document, TurnSource
from rag_access_guard_api.schemas.chat import (
    AvailableTurn,
    PendingTurn,
    SourceView,
    ThreadDetail,
    TurnView,
    UnavailableTurn,
)
from rag_access_guard_api.services.chat_repository import get_owned_thread
from rag_access_guard_api.services.chat_state import StoredTurn, neutral_view
from rag_access_guard_api.services.chat_turns import load_turns
from rag_access_guard_api.services.security import ReadUoW, database_clock, revalidate_session
from rag_access_guard_api.services.source_closure import closure_matches
from rag_access_guard_api.services.sources import build_source_url


class ReadPolicyUnavailableError(Exception):
    """Abort the protected projection without carrying adapter error details."""


async def read_thread(uow: ReadUoW, thread_id: UUID, *, session_token: str) -> ThreadDetail:
    """Project one owned snapshot; a policy outage hides every protected answer."""
    thread = await get_owned_thread(uow, thread_id, lock=True)
    await revalidate_session(uow, session_token)
    turns = await load_turns(uow, thread_id)
    try:
        views = tuple([await _project_turn(uow, turn) for turn in turns])
    except ReadPolicyUnavailableError:
        views = tuple(
            [
                _hidden(turn) if turn.state == "available" else await _project_turn(uow, turn)
                for turn in turns
            ]
        )
    return ThreadDetail(
        id=thread.id,
        title=thread.title,
        revision=thread.revision,
        created_at=thread.created_at,
        turns=views,
    )


async def read_turn(uow: ReadUoW, turn: StoredTurn) -> TurnView:
    """Use the same projection for a completed, owner-checked request replay."""
    try:
        return await _project_turn(uow, turn)
    except ReadPolicyUnavailableError:
        return _hidden(turn)


def _hidden(turn: StoredTurn) -> UnavailableTurn:
    return UnavailableTurn(id=turn.id, request_id=turn.request_id, user_input=turn.user_input)


async def _project_turn(uow: ReadUoW, turn: StoredTurn) -> TurnView:
    match turn.state:
        case "pending":
            now = await database_clock(uow.connection)
            if turn.lease_expires_at is None or now >= turn.lease_expires_at:
                return neutral_view(turn, "interrupted")
            return PendingTurn(id=turn.id, request_id=turn.request_id, user_input=turn.user_input)
        case "neutral":
            return neutral_view(turn, turn.neutral_reason or "interrupted")
        case "available":
            return await _read_answer(uow, turn)
        case _:
            assert_never(turn.state)


async def _read_answer(uow: ReadUoW, turn: StoredTurn) -> TurnView:
    hidden = _hidden(turn)
    if not turn.provenance_complete:
        return hidden
    rows = (
        await uow.connection.execute(
            select(
                TurnSource.document_id,
                TurnSource.document_version_id,
                TurnSource.chunk_id,
            )
            .where(TurnSource.turn_id == turn.id)
            .order_by(
                TurnSource.position.asc().nulls_last(),
                TurnSource.document_id,
                TurnSource.document_version_id,
                TurnSource.chunk_id,
            )
        )
    ).tuples()
    refs = tuple(SourceRef(document_id=d, document_version_id=v, chunk_id=c) for d, v, c in rows)
    if not closure_matches(refs, turn.source_closure_sha256):
        return hidden
    decision = await Guard(FakeTokenCounter()).authorize_read(
        uow.principal.principal_id,
        refs,
        PostgresPolicyReader(uow),
    )
    if decision.reason == "policy_unavailable":
        raise ReadPolicyUnavailableError
    if not decision.allowed:
        return hidden
    answer = (
        await uow.connection.execute(select(ChatTurn.answer).where(ChatTurn.id == turn.id))
    ).scalar_one()
    if answer is None:
        return hidden
    sources = tuple([await _source(uow, ref) for ref in refs])
    return AvailableTurn(
        id=turn.id,
        request_id=turn.request_id,
        user_input=turn.user_input,
        answer=answer,
        sources=sources,
    )


async def _source(uow: ReadUoW, ref: SourceRef) -> SourceView:
    title = (
        await uow.connection.execute(select(Document.title).where(Document.id == ref.document_id))
    ).scalar_one()
    return SourceView(
        document_id=ref.document_id,
        document_version_id=ref.document_version_id,
        chunk_id=ref.chunk_id,
        title=title,
        url=build_source_url(ref),
    )
