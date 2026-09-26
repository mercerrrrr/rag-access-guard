"""Conversation metadata scoped to the identity of a live transaction."""

from uuid import UUID, uuid4

from sqlalchemy import insert, select

from rag_access_guard import PriorTurn, SourceRef
from rag_access_guard_api.persistence import ChatThread, ChatTurn, TurnSource
from rag_access_guard_api.schemas.chat import ThreadView
from rag_access_guard_api.services.security import ReadUoW
from rag_access_guard_api.services.source_closure import closure_matches


class ThreadNotFound(Exception):  # noqa: N818
    """Foreign and missing identifiers have the same public outcome."""


async def create_thread(uow: ReadUoW) -> ThreadView:
    """Insert a server-owned empty thread without changing security policy."""
    row = (
        (
            await uow.connection.execute(
                insert(ChatThread)
                .values(id=uuid4(), owner_user_id=uow.principal.principal_id)
                .returning(
                    ChatThread.id, ChatThread.title, ChatThread.revision, ChatThread.created_at
                )
            )
        )
        .mappings()
        .one()
    )
    return ThreadView.model_validate(row)


async def list_threads(uow: ReadUoW) -> tuple[ThreadView, ...]:
    """Select only this principal's metadata in stable creation order."""
    rows = (
        await uow.connection.execute(
            select(ChatThread.id, ChatThread.title, ChatThread.revision, ChatThread.created_at)
            .where(ChatThread.owner_user_id == uow.principal.principal_id)
            .order_by(ChatThread.created_at, ChatThread.id)
        )
    ).mappings()
    return tuple(ThreadView.model_validate(row) for row in rows)


async def get_owned_thread(uow: ReadUoW, thread_id: UUID, *, lock: bool = False) -> ChatThread:
    """Restrict ownership before selecting or acquiring a conversation lock."""
    query = select(
        ChatThread.id,
        ChatThread.owner_user_id,
        ChatThread.title,
        ChatThread.revision,
        ChatThread.created_at,
    ).where(ChatThread.id == thread_id, ChatThread.owner_user_id == uow.principal.principal_id)
    if lock:
        query = query.with_for_update()
    row = (await uow.connection.execute(query)).tuples().one_or_none()
    if row is None:
        raise ThreadNotFound
    thread = ChatThread(id=row[0], owner_user_id=row[1], title=row[2], revision=row[3])
    thread.created_at = row[4]
    return thread


async def load_prior_turns(
    uow: ReadUoW, thread_id: UUID, *, limit: int = 4
) -> tuple[PriorTurn, ...]:
    """Load the fixed available window only after verifying the canonical thread owner."""
    _ = await get_owned_thread(uow, thread_id)
    rows = (
        (
            await uow.connection.execute(
                select(
                    ChatTurn.id,
                    ChatTurn.user_input,
                    ChatTurn.answer,
                    ChatTurn.provenance_complete,
                    ChatTurn.source_closure_sha256,
                )
                .where(ChatTurn.thread_id == thread_id, ChatTurn.state == "available")
                .order_by(ChatTurn.ordinal.desc())
                .limit(max(0, min(limit, 4)))
            )
        )
        .tuples()
        .all()
    )
    turns: list[PriorTurn] = []
    for turn_id, question, answer, complete, expected_closure in reversed(rows):
        sources = (
            await uow.connection.execute(
                select(TurnSource.document_id, TurnSource.document_version_id, TurnSource.chunk_id)
                .where(TurnSource.turn_id == turn_id)
                .order_by(
                    TurnSource.position.asc().nulls_last(),
                    TurnSource.document_id,
                    TurnSource.document_version_id,
                    TurnSource.chunk_id,
                )
            )
        ).tuples()
        refs = tuple(
            SourceRef(document_id=d, document_version_id=v, chunk_id=c) for d, v, c in sources
        )
        turns.append(
            PriorTurn(
                turn_id=turn_id,
                user_input=question,
                answer=answer or "",
                source_refs=refs,
                provenance_complete=complete and closure_matches(refs, expected_closure),
            )
        )
    return tuple(turns)
