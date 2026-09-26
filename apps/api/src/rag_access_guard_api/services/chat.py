"""Provenance-aware generation with no database locks across model inference."""

from dataclasses import dataclass, field
from typing import Final, assert_never
from uuid import UUID

import anyio

from rag_access_guard import Guard, PreparedContext, PrepareDenied, TokenCounter
from rag_access_guard.context import matches_history
from rag_access_guard_api.adapters import embeddings, llm
from rag_access_guard_api.adapters.policy import PostgresPolicyReader
from rag_access_guard_api.config import (
    GENERATION_MAX_ATTEMPTS,
    GENERATION_TIMEOUT_SECONDS,
    PENDING_LEASE_SECONDS,
)
from rag_access_guard_api.schemas.chat import MessageRequest, MessageResponse
from rag_access_guard_api.schemas.embedding_vectors import EmbeddingError
from rag_access_guard_api.schemas.search import SearchError
from rag_access_guard_api.services.chat_read import read_turn
from rag_access_guard_api.services.chat_repository import get_owned_thread, load_prior_turns
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
    ReadUoW,
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
    counter: TokenCounter


@dataclass(frozen=True, slots=True)
class Neutral:
    """A server-only completion with no model output."""

    attempt: Reservation
    reason: NeutralReason


@dataclass(frozen=True, slots=True)
class StaleGeneration:
    """Carry no discarded model output or prepared context into the next attempt."""


type Completion = MessageResponse | ChatConflict | StaleGeneration


@dataclass(frozen=True, slots=True)
class ChatService:
    """Bind one HTTP request's CSRF proof to each fresh session transaction."""

    policy: PolicyUnitOfWork
    csrf_token: str = field(repr=False)
    generation_timeout_seconds: int = GENERATION_TIMEOUT_SECONDS
    pending_lease_seconds: int = PENDING_LEASE_SECONDS

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
            reserved = await reserve(
                uow,
                thread_id,
                request,
                session_token=session_token,
                lease_seconds=self.pending_lease_seconds,
            )
        match reserved:
            case MessageResponse() | ChatConflict():
                return reserved
            case Reservation():
                for _ in range(GENERATION_MAX_ATTEMPTS):
                    completed = await self._attempt(session_token, reserved, request.user_input)
                    if not isinstance(completed, StaleGeneration):
                        return completed
                final = await self._complete(
                    session_token, Neutral(reserved, "policy_changed"), expected_attempt=reserved
                )
                if isinstance(final, StaleGeneration):
                    message = "Neutral completion cannot request generation"
                    raise TypeError(message)
                return final
            case _:
                assert_never(reserved)

    async def _attempt(
        self, session_token: str, reserved: Reservation, user_input: str
    ) -> Completion:
        outcome = await self._generate(session_token, reserved, user_input)
        expected: Reservation = reserved
        if isinstance(outcome, Generated):
            expected = GenerationAttempt(
                reserved.principal_id,
                reserved.session_id,
                reserved.thread_id,
                reserved.request_id,
                reserved.thread_revision,
                outcome.attempt.prepared,
            )
        return await self._complete(session_token, outcome, expected_attempt=expected)

    async def _generate(
        self,
        session_token: str,
        reservation: Reservation,
        user_input: str,
    ) -> Generated | Neutral:
        try:
            counter = llm.get_token_counter()
            if counter is None:
                return Neutral(reservation, "generation_unavailable")
            with anyio.fail_after(self.generation_timeout_seconds):
                vector = await embeddings.get_embedding_adapter().embed_query(user_input)
                async with self.policy.protected_read(session_token) as uow:
                    _ = await get_owned_thread(uow, reservation.thread_id)
                    if uow.principal.session_id != reservation.session_id:
                        raise ForbiddenError
                    chunks = await retrieve(uow, vector)
                    history = await load_prior_turns(uow, reservation.thread_id)
                    prepared = await Guard(counter).prepare_context(
                        reservation.principal_id,
                        chunks,
                        history,
                        PostgresPolicyReader(uow),
                    )
                match prepared:
                    case PrepareDenied():
                        return Neutral(reservation, "policy_changed")
                    case PreparedContext():
                        if not prepared.source_refs or not matches_history(prepared, history):
                            return Neutral(
                                reservation,
                                "policy_changed" if prepared.source_refs else "no_context",
                            )
                        attempt = GenerationAttempt(
                            reservation.principal_id,
                            reservation.session_id,
                            reservation.thread_id,
                            reservation.request_id,
                            reservation.thread_revision,
                            prepared,
                        )
                        return await self._infer(session_token, attempt, user_input, counter)
                    case _:
                        assert_never(prepared)
        except (llm.LLMUnavailableError, EmbeddingError, SearchError, TimeoutError):
            return Neutral(reservation, "generation_unavailable")

    async def _infer(
        self,
        session_token: str,
        attempt: GenerationAttempt,
        user_input: str,
        counter: TokenCounter,
    ) -> Generated | Neutral:
        reservation = Reservation(
            attempt.principal_id,
            attempt.session_id,
            attempt.thread_id,
            attempt.request_id,
            attempt.thread_revision,
        )
        if not await self._active(session_token, attempt):
            return Neutral(reservation, "interrupted")
        body = await llm.get_llm_adapter().generate(
            user_input=user_input, system_supplied_context=attempt.prepared.model_context
        )
        if not body.strip() or "\x00" in body or len(body.encode("utf-8")) > MAX_ANSWER_BYTES:
            return Neutral(reservation, "generation_unavailable")
        return Generated(attempt, body, counter)

    async def _active(self, session_token: str, reservation: Reservation) -> bool:
        async with self.policy.protected_read(session_token) as uow:
            if (
                uow.principal.principal_id != reservation.principal_id
                or uow.principal.session_id != reservation.session_id
                or not matches_token(self.csrf_token, uow.csrf_digest)
            ):
                raise ForbiddenError
            thread = await get_owned_thread(uow, reservation.thread_id, lock=True)
            await revalidate_session(uow, session_token)
            turn = next(
                (
                    t
                    for t in await load_turns(uow, thread.id)
                    if t.request_id == reservation.request_id
                ),
                None,
            )
            now = await database_clock(uow.connection)
            return (
                thread.revision == reservation.thread_revision
                and turn is not None
                and turn.expected_thread_revision == reservation.thread_revision
                and turn.state == "pending"
                and turn.lease_expires_at is not None
                and now < turn.lease_expires_at
            )

    async def _complete(
        self,
        session_token: str,
        outcome: Generated | Neutral,
        *,
        expected_attempt: Reservation,
    ) -> Completion:
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
                result = await _release_result(uow, outcome)
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


async def _release_result(
    uow: ReadUoW, outcome: Generated | Neutral
) -> ReleasedAnswer | NeutralReason | StaleGeneration:
    if isinstance(outcome, Neutral):
        return outcome.reason
    generated = outcome.attempt
    canonical_history = await load_prior_turns(uow, generated.thread_id)
    release = await Guard(outcome.counter).authorize_release(
        generated.principal_id,
        generated.thread_id,
        generated.prepared,
        PostgresPolicyReader(uow),
    )
    if not release.allowed and release.reason == "stale_revision":
        return StaleGeneration()
    if release.allowed and matches_history(generated.prepared, canonical_history):
        return ReleasedAnswer(outcome.body, generated.prepared.source_refs)
    return "policy_changed"
