"""Provenance-aware generation with no database locks across model inference."""

from dataclasses import dataclass, field
from functools import partial
from typing import Final, assert_never
from uuid import UUID

import anyio
from anyio.to_thread import run_sync

from rag_access_guard import Guard, PrepareDenied, TokenCounter
from rag_access_guard.context import matches_history
from rag_access_guard_api.adapters import embeddings, llm
from rag_access_guard_api.adapters.inference_runtime import (
    InferenceBusyError,
    InferenceRuntime,
    InferenceUnavailableError,
    current_runtime,
)
from rag_access_guard_api.adapters.policy import PostgresPolicyReader
from rag_access_guard_api.config import (
    GENERATION_MAX_ATTEMPTS,
    GENERATION_TIMEOUT_SECONDS,
    PENDING_LEASE_SECONDS,
)
from rag_access_guard_api.schemas.chat import MessageRequest, MessageResponse
from rag_access_guard_api.schemas.embedding_vectors import EmbeddingError
from rag_access_guard_api.schemas.search import SearchError
from rag_access_guard_api.services import chat_completion
from rag_access_guard_api.services.chat_completion import ChatCompletion
from rag_access_guard_api.services.chat_release import Generated, Neutral, StaleGeneration
from rag_access_guard_api.services.chat_repository import get_owned_thread, load_prior_turns
from rag_access_guard_api.services.chat_reservation import active_lease, reserve, resolve_request
from rag_access_guard_api.services.chat_state import ChatConflict, GenerationAttempt, Reservation
from rag_access_guard_api.services.errors import ForbiddenError
from rag_access_guard_api.services.origin import InvalidOriginError
from rag_access_guard_api.services.origin_context import (
    HostPrepared,
    origin_binding_matches,
    prepare_origin_context,
)
from rag_access_guard_api.services.query_validation import validate_query
from rag_access_guard_api.services.retrieval import retrieve
from rag_access_guard_api.services.security import PolicyUnitOfWork, database_clock
from rag_access_guard_api.services.tokens import matches_token

MAX_ANSWER_BYTES: Final = 65536
__all__ = ("ChatService", "Generated", "Neutral", "StaleGeneration")
Completion = chat_completion.Completion


@dataclass(frozen=True, slots=True)
class ChatService:
    """Bind one HTTP request's CSRF proof to each fresh session transaction."""

    policy: PolicyUnitOfWork
    csrf_token: str = field(repr=False)
    generation_timeout_seconds: int = GENERATION_TIMEOUT_SECONDS
    pending_lease_seconds: int = PENDING_LEASE_SECONDS
    runtime: InferenceRuntime = field(default_factory=current_runtime, repr=False, compare=False)

    async def generate_turn(
        self,
        session_token: str,
        thread_id: UUID,
        request: MessageRequest,
    ) -> MessageResponse | ChatConflict:
        """Reserve, prepare, generate, reauthorize and commit before returning."""
        async with self.policy.protected_read(session_token) as uow:
            principal_id = uow.principal.principal_id
            if not matches_token(self.csrf_token, uow.csrf_digest):
                raise ForbiddenError
            resolved = await resolve_request(uow, thread_id, request, session_token=session_token)
        if resolved is not None:
            return resolved
        counter = await run_sync(llm.get_token_counter)
        if counter is None:
            raise llm.LLMUnavailableError
        await run_sync(partial(validate_query, request.user_input, model_counter=counter))
        await llm.ensure_llm_available()
        async with self.runtime.try_acquire_generation(principal_id):
            return await self._reserve_and_generate(session_token, thread_id, request)

    async def _reserve_and_generate(
        self, session_token: str, thread_id: UUID, request: MessageRequest
    ) -> MessageResponse | ChatConflict:
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
                outcome.attempt.origin_binding,
                outcome.attempt.model_context,
                outcome.attempt.context_budget,
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
                    prepared = await prepare_origin_context(
                        uow,
                        chunks,
                        history,
                        counter,
                        user_input=user_input,
                    )
                match prepared:
                    case PrepareDenied():
                        return Neutral(reservation, "policy_changed")
                    case None:
                        return Neutral(reservation, "no_context")
                    case HostPrepared():
                        context = prepared.prepared
                        if not context.source_refs or not matches_history(context, history):
                            return Neutral(
                                reservation,
                                "policy_changed" if context.source_refs else "no_context",
                            )
                        attempt = GenerationAttempt(
                            reservation.principal_id,
                            reservation.session_id,
                            reservation.thread_id,
                            reservation.request_id,
                            reservation.thread_revision,
                            context,
                            prepared.origin_binding,
                            prepared.model_context,
                            prepared.context_budget,
                        )
                        return await self._infer(session_token, attempt, user_input, counter)
                    case _:
                        assert_never(prepared)
        except (
            llm.LLMUnavailableError,
            EmbeddingError,
            SearchError,
            TimeoutError,
            InferenceBusyError,
            InferenceUnavailableError,
            InvalidOriginError,
        ):
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
        if not await self._active(session_token, attempt, counter=counter):
            return Neutral(reservation, "interrupted")
        body = await llm.get_llm_adapter().generate(
            user_input=user_input,
            system_supplied_context=attempt.model_context or attempt.prepared.model_context,
        )
        if not body.strip() or "\x00" in body or len(body.encode("utf-8")) > MAX_ANSWER_BYTES:
            return Neutral(reservation, "generation_unavailable")
        return Generated(attempt, body, counter)

    async def _active(
        self, session_token: str, reservation: GenerationAttempt, *, counter: TokenCounter
    ) -> bool:
        async with self.policy.protected_read(session_token) as uow:
            if (
                uow.principal.principal_id != reservation.principal_id
                or uow.principal.session_id != reservation.session_id
                or not matches_token(self.csrf_token, uow.csrf_digest)
            ):
                raise ForbiddenError
            expiry = await active_lease(uow, reservation, session_token=session_token)
            now = await database_clock(uow.connection)
            if expiry is None or now >= expiry:
                return False
            decision = await Guard(counter).authorize_read(
                reservation.principal_id,
                reservation.prepared.source_refs,
                PostgresPolicyReader(uow),
            )
            if not decision.allowed:
                return False
            return await origin_binding_matches(
                uow,
                reservation.prepared,
                origin_binding=reservation.origin_binding,
                model_context=reservation.model_context,
            )

    async def _complete(
        self,
        session_token: str,
        outcome: Generated | Neutral,
        *,
        expected_attempt: Reservation,
    ) -> Completion:
        return await ChatCompletion(self.policy, self.csrf_token).complete(
            session_token, outcome, expected_attempt=expected_attempt
        )
