"""Canonical historical projections omitting only repeated source-policy checks."""

from uuid import UUID

from sqlalchemy import select

from experiments.baseline import canonical_hashes
from rag_access_guard import SourceRef
from rag_access_guard_api.persistence import ChatTurn, Document, DocumentChunk, TurnSource
from rag_access_guard_api.schemas.chat import (
    AvailableTurn,
    SourceView,
    ThreadDetail,
    UnavailableTurn,
)
from rag_access_guard_api.schemas.sources import SourceContent, SourceNotFound
from rag_access_guard_api.services.chat_read import read_turn
from rag_access_guard_api.services.chat_repository import get_owned_thread
from rag_access_guard_api.services.chat_state import StoredTurn
from rag_access_guard_api.services.chat_turns import load_turns
from rag_access_guard_api.services.security import ReadUoW, revalidate_session
from rag_access_guard_api.services.source_closure import closure_matches
from rag_access_guard_api.services.sources import build_source_url


async def baseline_source(uow: ReadUoW, ref: SourceRef) -> SourceContent:
    """Require the complete canonical tuple and exact content despite omitted grant gate."""
    if len(await canonical_hashes(uow, (ref,))) != 1:
        raise SourceNotFound
    title, text = (
        (
            await uow.connection.execute(
                select(Document.title, DocumentChunk.text)
                .join(DocumentChunk, DocumentChunk.document_id == Document.id)
                .where(
                    Document.id == ref.document_id,
                    DocumentChunk.document_version_id == ref.document_version_id,
                    DocumentChunk.id == ref.chunk_id,
                )
            )
        )
        .tuples()
        .one()
    )
    return SourceContent(
        document_id=ref.document_id,
        document_version_id=ref.document_version_id,
        chunk_id=ref.chunk_id,
        title=title,
        text=text,
    )


async def baseline_answer(uow: ReadUoW, turn: StoredTurn) -> AvailableTurn | UnavailableTurn:
    """Preserve the stored closure check and immutable source identities for old answers."""
    hidden = UnavailableTurn(id=turn.id, request_id=turn.request_id, user_input=turn.user_input)
    rows = (
        await uow.connection.execute(
            select(
                TurnSource.document_id,
                TurnSource.document_version_id,
                TurnSource.chunk_id,
            )
            .where(TurnSource.turn_id == turn.id)
            .order_by(TurnSource.position)
        )
    ).tuples()
    refs = tuple(SourceRef(document_id=d, document_version_id=v, chunk_id=c) for d, v, c in rows)
    if not turn.provenance_complete or not closure_matches(refs, turn.source_closure_sha256):
        return hidden
    if len(await canonical_hashes(uow, refs)) != len(refs):
        return hidden
    answer = (
        await uow.connection.execute(select(ChatTurn.answer).where(ChatTurn.id == turn.id))
    ).scalar_one()
    if answer is None:
        return hidden
    sources: list[SourceView] = []
    for ref in refs:
        source = await baseline_source(uow, ref)
        sources.append(
            SourceView(
                document_id=ref.document_id,
                document_version_id=ref.document_version_id,
                chunk_id=ref.chunk_id,
                title=source.title,
                url=build_source_url(ref),
            )
        )
    return AvailableTurn(
        id=turn.id,
        request_id=turn.request_id,
        user_input=turn.user_input,
        answer=answer,
        sources=tuple(sources),
    )


async def baseline_thread(uow: ReadUoW, thread_id: UUID, *, session_token: str) -> ThreadDetail:
    """Keep ownership and wall-clock session validation identical on historical reads."""
    thread = await get_owned_thread(uow, thread_id, lock=True)
    await revalidate_session(uow, session_token)
    turns = await load_turns(uow, thread_id)
    views = tuple(
        [
            await baseline_answer(uow, turn)
            if turn.state == "available"
            else await read_turn(uow, turn)
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
