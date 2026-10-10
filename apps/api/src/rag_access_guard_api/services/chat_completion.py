"""Complete a bound chat attempt and return its projection only after commit."""

from dataclasses import dataclass, field

from rag_access_guard_api.schemas.chat import MessageResponse
from rag_access_guard_api.services.chat_read import read_turn
from rag_access_guard_api.services.chat_release import (
    Generated,
    Neutral,
    StaleGeneration,
    release_result,
)
from rag_access_guard_api.services.chat_repository import get_owned_thread
from rag_access_guard_api.services.chat_state import ChatConflict, NeutralReason, Reservation
from rag_access_guard_api.services.chat_turns import ReleasedAnswer, complete_turn, load_turns
from rag_access_guard_api.services.errors import ForbiddenError
from rag_access_guard_api.services.security import (
    PolicyUnitOfWork,
    database_clock,
    revalidate_session,
)
from rag_access_guard_api.services.tokens import matches_token

type Completion = MessageResponse | ChatConflict | StaleGeneration


@dataclass(frozen=True, slots=True)
class ChatCompletion:
    """Own release authorization, atomic completion and commit-before-delivery."""

    policy: PolicyUnitOfWork
    csrf_token: str = field(repr=False)

    async def complete(
        self,
        session_token: str,
        outcome: Generated | Neutral,
        *,
        expected_attempt: Reservation,
    ) -> Completion:
        """Return the authorized completion only after the protected UoW commits."""
        attempt = outcome.attempt
        if attempt != expected_attempt:
            raise ForbiddenError
        async with self.policy.protected_read(session_token) as uow:
            if (
                uow.principal.principal_id != attempt.principal_id
                or uow.principal.session_id != attempt.session_id
                or not matches_token(self.csrf_token, uow.csrf_digest)
            ):
                raise ForbiddenError
            thread = await get_owned_thread(uow, attempt.thread_id, lock=True)
            await revalidate_session(uow, session_token)
            turns = await load_turns(uow, thread.id)
            turn = next((t for t in turns if t.request_id == attempt.request_id), None)
            if turn is None or turn.expected_thread_revision != attempt.thread_revision:
                return ChatConflict("request_conflict")
            if turn.state != "pending":
                return MessageResponse(
                    thread_revision=thread.revision, turn=await read_turn(uow, turn), replayed=True
                )
            if thread.revision != attempt.thread_revision:
                return ChatConflict("thread_conflict")
            now = await database_clock(uow.connection)
            result: ReleasedAnswer | NeutralReason | StaleGeneration = "interrupted"
            if turn.lease_expires_at is not None and now < turn.lease_expires_at:
                result = await release_result(uow, outcome)
            await revalidate_session(uow, session_token)
            if (
                turn.lease_expires_at is None
                or await database_clock(uow.connection) >= turn.lease_expires_at
            ):
                result = "interrupted"
            response: Completion
            if isinstance(result, StaleGeneration):
                response = result
            else:
                revision = await complete_turn(uow, turn, result)
                completed = next(t for t in await load_turns(uow, thread.id) if t.id == turn.id)
                response = MessageResponse(
                    thread_revision=revision, turn=await read_turn(uow, completed), replayed=False
                )
        return response
