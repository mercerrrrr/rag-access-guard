"""Idempotent reservation ordering, including lazy crash recovery."""

from datetime import datetime
from typing import assert_never
from uuid import UUID

from rag_access_guard_api.config import PENDING_LEASE_SECONDS
from rag_access_guard_api.schemas.chat import MessageRequest, MessageResponse
from rag_access_guard_api.services.chat_read import read_turn
from rag_access_guard_api.services.chat_repository import get_owned_thread
from rag_access_guard_api.services.chat_state import ChatConflict, Reservation, request_hash
from rag_access_guard_api.services.chat_turns import complete_turn, insert_pending, load_turns
from rag_access_guard_api.services.security import ReadUoW, database_clock, revalidate_session


async def active_lease(
    uow: ReadUoW, reservation: Reservation, *, session_token: str
) -> datetime | None:
    """Read the matching pending lease under its thread lock before inference."""
    thread = await get_owned_thread(uow, reservation.thread_id, lock=True)
    await revalidate_session(uow, session_token)
    turn = next(
        (t for t in await load_turns(uow, thread.id) if t.request_id == reservation.request_id),
        None,
    )
    if (
        thread.revision == reservation.thread_revision
        and turn is not None
        and turn.expected_thread_revision == reservation.thread_revision
        and turn.state == "pending"
    ):
        return turn.lease_expires_at
    return None


async def resolve_request(  # noqa: PLR0911 -- each protocol outcome is explicit.
    uow: ReadUoW,
    thread_id: UUID,
    request: MessageRequest,
    *,
    session_token: str,
) -> MessageResponse | ChatConflict | None:
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
    return None


async def reserve(
    uow: ReadUoW,
    thread_id: UUID,
    request: MessageRequest,
    *,
    session_token: str,
    lease_seconds: int = PENDING_LEASE_SECONDS,
) -> Reservation | MessageResponse | ChatConflict:
    """Repeat resolution under the thread lock after unlocked query preflight."""
    resolved = await resolve_request(uow, thread_id, request, session_token=session_token)
    if resolved is not None:
        return resolved
    thread = await get_owned_thread(uow, thread_id)
    await insert_pending(uow, thread_id, request, lease_seconds=lease_seconds)
    return Reservation(
        uow.principal.principal_id,
        uow.principal.session_id,
        thread_id,
        request.request_id,
        thread.revision,
    )
