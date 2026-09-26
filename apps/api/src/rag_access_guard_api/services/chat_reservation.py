"""Idempotent reservation ordering, including lazy crash recovery."""

from typing import assert_never
from uuid import UUID

from rag_access_guard_api.schemas.chat import MessageRequest, MessageResponse
from rag_access_guard_api.services.chat_read import read_turn
from rag_access_guard_api.services.chat_repository import get_owned_thread
from rag_access_guard_api.services.chat_state import ChatConflict, Reservation, request_hash
from rag_access_guard_api.services.chat_turns import complete_turn, insert_pending, load_turns
from rag_access_guard_api.services.security import ReadUoW, database_clock, revalidate_session


async def reserve(  # noqa: PLR0911 -- each protocol outcome is explicit.
    uow: ReadUoW,
    thread_id: UUID,
    request: MessageRequest,
    *,
    session_token: str,
) -> Reservation | MessageResponse | ChatConflict:
    """Resolve retries before optimistic revision, and commit expiry before any conflict."""
    thread = await get_owned_thread(uow, thread_id, lock=True)
    await revalidate_session(uow, session_token)
    turns = await load_turns(uow, thread_id)
    existing = next((t for t in turns if t.request_id == request.request_id), None)
    if existing is not None:
        if existing.request_sha256 != request_hash(request):
            return ChatConflict("request_conflict")
        match existing.state:
            case "available" | "neutral":
                return MessageResponse(
                    thread_revision=thread.revision,
                    turn=await read_turn(uow, existing),
                    replayed=True,
                )
            case "pending":
                now = await database_clock(uow.connection)
                if existing.lease_expires_at is not None and now < existing.lease_expires_at:
                    return ChatConflict("request_in_progress")
                revision = await complete_turn(uow, existing, "interrupted")
                return MessageResponse(
                    thread_revision=revision, turn=await read_turn(uow, existing), replayed=True
                )
            case _:
                assert_never(existing.state)
    revision = thread.revision
    for turn in turns:
        if turn.state == "pending":
            now = await database_clock(uow.connection)
            if turn.lease_expires_at is not None and now < turn.lease_expires_at:
                return ChatConflict("request_in_progress")
            revision = await complete_turn(uow, turn, "interrupted")
    if request.expected_thread_revision != revision:
        return ChatConflict("thread_conflict")
    await insert_pending(uow, thread_id, request)
    return Reservation(
        uow.principal.principal_id,
        uow.principal.session_id,
        thread_id,
        request.request_id,
        revision,
    )
