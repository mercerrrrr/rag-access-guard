"""Shared transactional host for the isolated comparison, never imported by the API."""

import re
from dataclasses import dataclass, replace
from time import perf_counter
from typing import final
from uuid import uuid4

from experiments.attempts import bound_turn, lease_active
from experiments.baseline import canonical_hashes, prepare_baseline
from experiments.boundary_capture import CanonicalEvidence, capture_request
from experiments.faults import UnavailablePolicy, inject_candidate
from experiments.host_release import finish_answer
from experiments.host_state import HostState, PendingAnswer
from experiments.input_boundary import observe_boundary
from experiments.lineage import request_origins, selected_origins
from experiments.observations import AttemptEvidence, AttemptStatus, Observation
from experiments.seed import SyntheticEmbedder
from experiments.timing import StageClock
from rag_access_guard import Guard, PolicyReader, PrepareDenied
from rag_access_guard_api.adapters.ollama import OllamaAdapter
from rag_access_guard_api.adapters.policy import PostgresPolicyReader
from rag_access_guard_api.config import Settings
from rag_access_guard_api.schemas.chat import (
    MessageRequest,
    MessageResponse,
)
from rag_access_guard_api.services.chat_repository import (
    create_thread,
    load_prior_turns,
)
from rag_access_guard_api.services.chat_reservation import reserve
from rag_access_guard_api.services.chat_state import Reservation
from rag_access_guard_api.services.errors import ForbiddenError
from rag_access_guard_api.services.retrieval import retrieve
from rag_access_guard_api.services.tokens import matches_token


class ContextModel:
    """Controlled fake emits only markers actually received through the system channel."""

    async def generate(self, *, user_input: str, system_supplied_context: str) -> str:
        """No hidden history, retrieval or instruction execution."""
        del user_input
        return (
            " ".join(dict.fromkeys(re.findall(r"SYNTHETIC_[A-Z0-9_]+", system_supplied_context)))
            or "Synthetic answer"
        )


@dataclass
@final
class ExperimentHost(HostState):
    """Use ordinary sessions, ownership and persistence on the experiment database."""

    async def ask(self, action_id: str, question: str) -> MessageResponse:
        """Reserve and persist a real canonical pair around an unlocked model call."""
        started = perf_counter()
        async with self.policy.protected_read(self.actor.token) as uow:
            if not matches_token(self.actor.csrf, uow.csrf_digest):
                raise ForbiddenError
            if self.thread_id is None:
                self.thread_id = (await create_thread(uow)).id
            request = MessageRequest(
                request_id=uuid4(), expected_thread_revision=self.revision, user_input=question
            )
            reservation = await reserve(
                uow, self.thread_id, request, session_token=self.actor.token
            )
        if not isinstance(reservation, Reservation):
            message = "Fresh experiment reservation failed"
            raise TypeError(message)
        for attempt in range(2):
            first = len(self.observations)
            attempt_started = perf_counter()
            stages = StageClock(logical_started=started)
            status: AttemptStatus = "failed"
            try:
                response = await self._attempt(
                    action_id, request, reservation, stages, retry=attempt == 0
                )
                status = "stale_discarded" if response is None else "completed"
            finally:
                duration = (perf_counter() - attempt_started) * 1000
                self.attempts.append(
                    AttemptEvidence(
                        action_id=action_id,
                        number=attempt + 1,
                        status=status,
                        elapsed_ms=duration,
                        stage_ms=tuple(stages.values),
                        model_called=any(o.request is not None for o in self.observations[first:]),
                    )
                )
                for index in range(first, len(self.observations)):
                    self.observations[index] = replace(
                        self.observations[index],
                        attempt_number=attempt + 1,
                        attempt_status=status,
                        attempt_elapsed_ms=duration,
                    )
            if response is not None:
                return response
        message = "Last attempt must produce a terminal result"
        raise RuntimeError(message)

    async def _attempt(
        self,
        action_id: str,
        request: MessageRequest,
        reservation: Reservation,
        stages: StageClock,
        *,
        retry: bool,
    ) -> MessageResponse | None:
        started = stages.logical_started
        question = request.user_input
        counter = self.config.token_counter()
        guard = Guard(counter, self.config.max_context_tokens, self.config.max_prior_turns)
        with stages.measure("query_embedding"):
            vector = await SyntheticEmbedder().embed_query(question)
        async with self.policy.protected_read(self.actor.token) as uow:
            _ = await bound_turn(uow, self.actor, reservation, request)
            with stages.measure("retrieval"):
                chunks = await retrieve(uow, vector, limit=self.config.top_k)
            if self.fault is not None:
                chunks = await inject_candidate(uow, self.fault, self.corpus.documents)
                self.fault = None
            reader: PolicyReader = (
                UnavailablePolicy() if self.policy_failure else PostgresPolicyReader(uow)
            )
            self.policy_failure = False
            with stages.measure("prepare"):
                history = await load_prior_turns(uow, reservation.thread_id)
                prepared = (
                    await guard.prepare_context(self.actor.user_id, chunks, history, reader)
                    if self.arm == "guarded"
                    else await prepare_baseline(uow, chunks, history, self.config, reader)
                )
                if isinstance(prepared, PrepareDenied):
                    prepared = await guard.prepare_context(
                        self.actor.user_id, (), (), PostgresPolicyReader(uow)
                    )
                    if isinstance(prepared, PrepareDenied):
                        message = "Canonical empty preparation failed"
                        raise TypeError(message)
            snapshot = await PostgresPolicyReader(uow).snapshot(
                self.actor.user_id, prepared.source_refs, thread_id=reservation.thread_id
            )
            hashes = await canonical_hashes(uow, prepared.source_refs)
        captured = None
        body = ""
        async with self.policy.protected_read(self.actor.token) as uow:
            turn = await bound_turn(uow, self.actor, reservation, request)
            active = await lease_active(uow, turn)
        _ = self.witnesses.setdefault(action_id, prepared.source_refs)
        if prepared.source_refs and active:
            captured = capture_request(
                user_input=question,
                system_supplied_context=prepared.model_context,
                prepared=prepared,
                evidence=CanonicalEvidence(
                    snapshot=snapshot, history=history, canonical_chunk_hashes=hashes
                ),
                user_origin_markers=request_origins(
                    question,
                    self.synthetic_markers,
                    selected_origins(prepared, history, self.origins),
                ),
            )
        capture_index = len(self.observations)
        self.observations.append(
            Observation(
                action_id=action_id,
                surface="model_context",
                source_refs=prepared.source_refs,
                forbidden_refs=snapshot.denied_refs,
                provenance_valid=captured.provenance_valid
                if captured
                else snapshot.provenance_valid,
                elapsed_ms=(perf_counter() - started) * 1000,
                request=captured,
                markers=tuple(
                    (marker, observe_boundary(captured, output=body, marker=marker))
                    for marker in sorted(self.synthetic_markers)
                )
                if captured
                else (),
            )
        )
        if captured is not None:
            with stages.measure("llm"):
                model = (
                    ContextModel()
                    if self.config.model_manifest is None
                    else OllamaAdapter(
                        base_url=Settings().ollama_base_url, manifest=self.config.model_manifest
                    )
                )
                body = await model.generate(
                    user_input=question, system_supplied_context=prepared.model_context
                )
            self.observations[capture_index] = replace(
                self.observations[capture_index],
                body=body,
                markers=tuple(
                    (marker, observe_boundary(captured, output=body, marker=marker))
                    for marker in sorted(self.synthetic_markers)
                ),
            )
        if self.ordering is not None:
            await self.ordering.boundary(action_id, "model_generated")
        with stages.measure("release"):
            return await finish_answer(
                self,
                PendingAnswer(
                    reservation=reservation,
                    prepared=prepared,
                    body=body,
                    captured=captured,
                    request=request,
                    action_id=action_id,
                    started=started,
                    retry=retry,
                ),
            )
