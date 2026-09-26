"""Reauthorize each stored answer before loading any protected response fields."""

from typing import assert_never

from sqlalchemy import select

from rag_access_guard import Guard, SourceRef
from rag_access_guard_api.adapters.llm import FakeTokenCounter
from rag_access_guard_api.adapters.policy import PostgresPolicyReader
from rag_access_guard_api.persistence import ChatTurn, Document, TurnSource
from rag_access_guard_api.schemas.chat import (
    AvailableTurn,
    PendingTurn,
    SourceView,
    TurnView,
    UnavailableTurn,
)
from rag_access_guard_api.services.chat_state import StoredTurn, neutral_view
from rag_access_guard_api.services.security import ReadUoW, database_clock


async def read_turn(uow: ReadUoW, turn: StoredTurn) -> TurnView:
    """Project a turn within a live owner-checked policy snapshot."""
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
    hidden = UnavailableTurn(id=turn.id, request_id=turn.request_id, user_input=turn.user_input)
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
                TurnSource.document_id,
                TurnSource.document_version_id,
                TurnSource.chunk_id,
            )
        )
    ).tuples()
    refs = tuple(SourceRef(document_id=d, document_version_id=v, chunk_id=c) for d, v, c in rows)
    decision = await Guard(FakeTokenCounter()).authorize_read(
        uow.principal.principal_id,
        refs,
        PostgresPolicyReader(uow),
    )
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
        url=f"/api/documents/{ref.document_id}/versions/{ref.document_version_id}/content?chunk_id={ref.chunk_id}",
    )
