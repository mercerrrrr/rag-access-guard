"""Single-turn generation with no database locks across model inference."""

from dataclasses import dataclass, field
from typing import Final, assert_never
from uuid import UUID

import anyio

from rag_access_guard import Guard, PreparedContext, PrepareDenied
from rag_access_guard_api.adapters import embeddings, llm
from rag_access_guard_api.adapters.policy import PostgresPolicyReader
from rag_access_guard_api.schemas.chat import MessageRequest, MessageResponse
from rag_access_guard_api.schemas.embedding_vectors import EmbeddingError
from rag_access_guard_api.schemas.search import SearchError
from rag_access_guard_api.services.chat_read import read_turn
from rag_access_guard_api.services.chat_repository import get_owned_thread
from rag_access_guard_api.services.chat_reservation import reserve
from rag_access_guard_api.services.chat_state import (
    ChatConflict,
    GenerationAttempt,
    NeutralReason,
    Reservation,
)
from rag_access_guard_api.services.chat_turns import ReleasedAnswer, complete_turn, load_turns
from rag_access_guard_api.services.errors import ForbiddenError
from rag_access_guard_api.services.retrieval import retrieve
from rag_access_guard_api.services.security import (
    PolicyUnitOfWork,
    database_clock,
    revalidate_session,
)
from rag_access_guard_api.services.tokens import matches_token

MAX_ANSWER_BYTES: Final = 65536


@dataclass(frozen=True, slots=True)
class Generated:
    """Unreleased model output, never serialized or logged."""

    attempt: GenerationAttempt
    body: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class Neutral:
    """A server-only completion with no model output."""

    attempt: Reservation
    reason: NeutralReason


@dataclass(frozen=True, slots=True)
class ChatService:
    """Bind one HTTP request's CSRF proof to each fresh session transaction."""

    policy: PolicyUnitOfWork
    csrf_token: str = field(repr=False)

    async def generate_turn(
        self,
        session_token: str,
        thread_id: UUID,
        request: MessageRequest,
    ) -> MessageResponse | ChatConflict:
        """Reserve, prepare, generate, reauthorize and commit before returning."""
        async with self.policy.protected_read(session_token) as uow:
            if not matches_token(self.csrf_token, uow.csrf_digest):
                raise ForbiddenError
            reserved = await reserve(uow, thread_id, request, session_token=session_token)
        match reserved:
            case MessageResponse() | ChatConflict():
                return reserved
            case Reservation():
                outcome = await self._generate(session_token, reserved, request.user_input)
                return await self._complete(session_token, outcome)
            case _:
                assert_never(reserved)

    async def _generate(
        self,
        session_token: str,
        reservation: Reservation,
        user_input: str,
    ) -> Generated | Neutral:
        try:
            adapter = llm.get_llm_adapter()
            with anyio.fail_after(60):
                vector = await embeddings.get_embedding_adapter().embed_query(user_input)
                async with self.policy.protected_read(session_token) as uow:
                    _ = await get_owned_thread(uow, reservation.thread_id)
                    if uow.principal.session_id != reservation.session_id:
                        raise ForbiddenError
                    chunks = await retrieve(uow, vector)
                    prepared = await Guard(llm.FakeTokenCounter()).prepare_context(
                        reservation.principal_id,
                        chunks,
                        (),
                        PostgresPolicyReader(uow),
                    )
                match prepared:
                    case PrepareDenied():
                        return Neutral(reservation, "policy_changed")
                    case PreparedContext():
                        if not prepared.source_refs:
                            return Neutral(reservation, "no_context")
                        attempt = GenerationAttempt(
                            reservation.principal_id,
                            reservation.session_id,
                            reservation.thread_id,
                            reservation.request_id,
                            reservation.thread_revision,
                            prepared,
                        )
                        body = await adapter.generate(
                            user_input=user_input, system_supplied_context=prepared.model_context
                        )
                        if (
                            not body.strip()
                            or "\x00" in body
                            or len(body.encode("utf-8")) > MAX_ANSWER_BYTES
                        ):
                            return Neutral(reservation, "generation_unavailable")
                        return Generated(attempt, body)
                    case _:
                        assert_never(prepared)
        except (llm.LLMUnavailableError, EmbeddingError, SearchError, TimeoutError):
            return Neutral(reservation, "generation_unavailable")

    async def _complete(
        self, session_token: str, outcome: Generated | Neutral
    ) -> MessageResponse | ChatConflict:
        attempt = outcome.attempt
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
            result: ReleasedAnswer | NeutralReason = "interrupted"
            if turn.lease_expires_at is not None and now < turn.lease_expires_at:
                match outcome:
                    case Generated(attempt=generated, body=body):
                        release = await Guard(llm.FakeTokenCounter()).authorize_release(
                            generated.principal_id,
                            generated.thread_id,
                            generated.prepared,
                            PostgresPolicyReader(uow),
                        )
                        result = (
                            ReleasedAnswer(body, generated.prepared.source_refs)
                            if release.allowed
                            else "policy_changed"
                        )
                    case Neutral(reason=reason):
                        result = reason
                    case _:
                        assert_never(outcome)
            await revalidate_session(uow, session_token)
            if (
                turn.lease_expires_at is None
                or await database_clock(uow.connection) >= turn.lease_expires_at
            ):
                result = "interrupted"
            revision = await complete_turn(uow, turn, result)
            completed = next(t for t in await load_turns(uow, thread.id) if t.id == turn.id)
            response = MessageResponse(
                thread_revision=revision, turn=await read_turn(uow, completed), replayed=False
            )
        return response  # noqa: RET504 -- the transaction must commit before returning.
