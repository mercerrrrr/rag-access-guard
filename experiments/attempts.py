"""Host binding and leases are identical in baseline and guarded experiments."""

from experiments.seed import Actor
from rag_access_guard_api.schemas.chat import MessageRequest
from rag_access_guard_api.services.chat_repository import get_owned_thread
from rag_access_guard_api.services.chat_state import Reservation, StoredTurn, request_hash
from rag_access_guard_api.services.chat_turns import load_turns
from rag_access_guard_api.services.errors import ForbiddenError
from rag_access_guard_api.services.security import ReadUoW, database_clock, revalidate_session
from rag_access_guard_api.services.tokens import matches_token


async def bound_turn(
    uow: ReadUoW,
    actor: Actor,
    reservation: Reservation,
    request: MessageRequest,
) -> StoredTurn:
    """Reject changed session, request identity, thread revision or pending ownership."""
    if (
        uow.principal.principal_id != reservation.principal_id
        or uow.principal.session_id != reservation.session_id
        or not matches_token(actor.csrf, uow.csrf_digest)
        or request.request_id != reservation.request_id
        or request.expected_thread_revision != reservation.thread_revision
    ):
        raise ForbiddenError
    thread = await get_owned_thread(uow, reservation.thread_id, lock=True)
    await revalidate_session(uow, actor.token)
    turn = next(
        (t for t in await load_turns(uow, thread.id) if t.request_id == reservation.request_id),
        None,
    )
    if (
        turn is None
        or turn.state != "pending"
        or turn.expected_thread_revision != reservation.thread_revision
        or thread.revision != reservation.thread_revision
        or turn.request_sha256 != request_hash(request)
    ):
        raise ForbiddenError
    return turn


async def lease_active(uow: ReadUoW, turn: StoredTurn) -> bool:
    """Use database wall time before inference and on both sides of the release check."""
    return (
        turn.lease_expires_at is not None
        and await database_clock(uow.connection) < turn.lease_expires_at
    )
