"""Shared transactional host for the isolated comparison, never imported by the API."""

import re
from dataclasses import dataclass, field, replace
from time import perf_counter
from uuid import UUID, uuid4

from experiments.attempts import bound_turn, lease_active
from experiments.baseline import baseline_integrity, canonical_hashes, prepare_baseline
from experiments.baseline_reads import baseline_answer, baseline_source, baseline_thread
from experiments.boundary_capture import CanonicalEvidence, capture_request
from experiments.comparison_config import ComparisonConfig
from experiments.faults import UnavailablePolicy, inject_candidate
from experiments.input_boundary import ModelRequestObservation, observe_boundary
from experiments.lineage import request_origins, selected_origins
from experiments.observations import Arm, Observation
from experiments.ordering import Ordering
from experiments.scenario_types import Principal, TamperProvenance
from experiments.seed import Actor, SeededCorpus, SyntheticEmbedder
from rag_access_guard import Guard, PolicyReader, PreparedContext, PrepareDenied, SourceRef
from rag_access_guard.context import matches_history
from rag_access_guard_api.adapters.llm import FakeTokenCounter
from rag_access_guard_api.adapters.policy import PostgresPolicyReader
from rag_access_guard_api.schemas.access import AccessibleDocuments
from rag_access_guard_api.schemas.chat import (
    AvailableTurn,
    MessageRequest,
    MessageResponse,
    ThreadDetail,
)
from rag_access_guard_api.schemas.sources import SourceContent, SourceNotFound
from rag_access_guard_api.services.access import list_accessible_documents
from rag_access_guard_api.services.chat_read import read_thread, read_turn
from rag_access_guard_api.services.chat_repository import (
    create_thread,
    load_prior_turns,
)
from rag_access_guard_api.services.chat_reservation import reserve
from rag_access_guard_api.services.chat_state import Reservation
from rag_access_guard_api.services.chat_turns import ReleasedAnswer, complete_turn, load_turns
from rag_access_guard_api.services.errors import ForbiddenError
from rag_access_guard_api.services.retrieval import retrieve
from rag_access_guard_api.services.security import PolicyUnitOfWork, revalidate_session
from rag_access_guard_api.services.sources import read_source
from rag_access_guard_api.services.tokens import matches_token


@dataclass(frozen=True, slots=True)
class PendingAnswer:
    """Unreleased bytes remain bound to their exact reservation and prepared closure."""

    reservation: Reservation
    prepared: PreparedContext
    body: str = field(repr=False)
    captured: ModelRequestObservation | None = field(default=None, repr=False)


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
class ExperimentHost:
    """Use ordinary sessions, ownership and persistence on the experiment database."""

    policy: PolicyUnitOfWork
    corpus: SeededCorpus
    actor: Actor
    config: ComparisonConfig
    arm: Arm
    synthetic_markers: frozenset[str] = frozenset()
    thread_id: UUID | None = None
    revision: int = 0
    observations: list[Observation] = field(default_factory=list)
    witnesses: dict[str, tuple[SourceRef, ...]] = field(default_factory=dict)
    fault: TamperProvenance | None = None
    policy_failure: bool = False
    ordering: Ordering | None = None
    origins: dict[UUID, frozenset[str]] = field(default_factory=dict)

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
            response = await self._attempt(
                action_id, request, reservation, started, retry=attempt == 0
            )
            duration = (perf_counter() - attempt_started) * 1000
            for index in range(first, len(self.observations)):
                self.observations[index] = replace(
                    self.observations[index],
                    attempt_number=attempt + 1,
                    attempt_status="stale_discarded" if response is None else "completed",
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
        started: float,
        *,
        retry: bool,
    ) -> MessageResponse | None:
        question = request.user_input
        counter = FakeTokenCounter()
        guard = Guard(counter, self.config.max_context_tokens, self.config.max_prior_turns)
        vector = await SyntheticEmbedder().embed_query(question)
        async with self.policy.protected_read(self.actor.token) as uow:
            _ = await bound_turn(uow, self.actor, reservation, request)
            chunks = await retrieve(uow, vector, limit=self.config.top_k)
            if self.fault is not None:
                chunks = await inject_candidate(uow, self.fault, self.corpus.documents)
                self.fault = None
            reader: PolicyReader = (
                UnavailablePolicy() if self.policy_failure else PostgresPolicyReader(uow)
            )
            self.policy_failure = False
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
            body = await ContextModel().generate(
                user_input=question, system_supplied_context=prepared.model_context
            )
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
        if self.ordering is not None:
            await self.ordering.boundary(action_id, "model_generated")
        return await self.finish(
            action_id,
            request,
            PendingAnswer(reservation, prepared, body, captured),
            started,
            retry=retry,
        )

    async def finish(
        self,
        action_id: str,
        request: MessageRequest,
        pending: PendingAnswer,
        started: float,
        *,
        retry: bool,
    ) -> MessageResponse | None:
        """Commit answer and closure only inside a fresh session/thread/policy gate."""
        reservation, prepared, body = pending.reservation, pending.prepared, pending.body
        async with self.policy.protected_read(self.actor.token) as uow:
            turn = await bound_turn(uow, self.actor, reservation, request)
            active = await lease_active(uow, turn)
            snapshot = await PostgresPolicyReader(uow).snapshot(
                self.actor.user_id, prepared.source_refs, thread_id=reservation.thread_id
            )
            release = None
            if self.arm == "guarded" and body and active:
                release = await Guard(
                    FakeTokenCounter(),
                    self.config.max_context_tokens,
                    self.config.max_prior_turns,
                ).authorize_release(
                    self.actor.user_id, reservation.thread_id, prepared, PostgresPolicyReader(uow)
                )
            integrity = (
                await baseline_integrity(
                    uow, prepared, await load_prior_turns(uow, reservation.thread_id), self.config
                )
                if self.arm == "baseline" and body
                else False
            )
            allowed = bool(
                active
                and body
                and prepared.source_refs
                and (
                    release is not None
                    and release.allowed
                    and matches_history(
                        prepared, await load_prior_turns(uow, reservation.thread_id)
                    )
                    if self.arm == "guarded"
                    else integrity
                )
            )
            await revalidate_session(uow, self.actor.token)
            active = active and await lease_active(uow, turn)
            if active and release is not None and release.reason == "stale_revision" and retry:
                return None
            allowed = allowed and active
            self.revision = await complete_turn(
                uow,
                turn,
                ReleasedAnswer(body, prepared.source_refs)
                if allowed
                else "no_context"
                if active
                else "interrupted",
            )
            completed = next(
                t for t in await load_turns(uow, reservation.thread_id) if t.id == turn.id
            )
            view = (
                await baseline_answer(uow, completed)
                if self.arm == "baseline" and completed.state == "available"
                else await read_turn(uow, completed)
            )
            response = MessageResponse(thread_revision=self.revision, turn=view, replayed=False)
        refs = prepared.source_refs if allowed else ()
        if allowed and pending.captured is not None:
            self.origins[completed.id] = pending.captured.user_origin_markers
        if self.ordering is not None:
            self.ordering.commits.append(action_id)
            await self.ordering.boundary(action_id, "release_committed")
        self.observations.append(
            Observation(
                action_id=action_id,
                surface="release",
                source_refs=refs,
                forbidden_refs=tuple(ref for ref in refs if ref in snapshot.denied_refs),
                provenance_valid=snapshot.provenance_valid,
                elapsed_ms=(perf_counter() - started) * 1000,
                body=body if allowed else "",
                persisted="available" if completed.state == "available" else "neutral",
                markers=tuple(
                    (
                        marker,
                        observe_boundary(
                            pending.captured, output=body if allowed else "", marker=marker
                        ),
                    )
                    for marker in sorted(self.synthetic_markers)
                )
                if pending.captured
                else (),
            )
        )
        return response

    async def stored_read(self, action_id: str, reader: Principal) -> ThreadDetail:
        """Observe the actual protected read projection without changing stored rows."""
        if self.thread_id is None:
            message = "No experiment thread"
            raise RuntimeError(message)
        started = perf_counter()
        actor = self.corpus.actors[reader]
        async with self.policy.protected_read(actor.token) as uow:
            detail = (
                await read_thread(uow, self.thread_id, session_token=actor.token)
                if self.arm == "guarded"
                else await baseline_thread(uow, self.thread_id, session_token=actor.token)
            )
            visible = tuple(t for t in detail.turns if isinstance(t, AvailableTurn))
            refs = tuple(
                dict.fromkeys(
                    SourceRef(
                        document_id=s.document_id,
                        document_version_id=s.document_version_id,
                        chunk_id=s.chunk_id,
                    )
                    for t in visible
                    for s in t.sources
                )
            )
            snapshot = await PostgresPolicyReader(uow).snapshot(actor.user_id, refs)
        self.observations.append(
            Observation(
                action_id=action_id,
                surface="stored_read",
                source_refs=refs,
                forbidden_refs=snapshot.denied_refs,
                provenance_valid=snapshot.provenance_valid,
                elapsed_ms=(perf_counter() - started) * 1000,
                body="\n".join(t.answer for t in visible),
                titles=tuple(s.title for t in visible for s in t.sources),
            )
        )
        return detail

    async def document_list(self, action_id: str) -> AccessibleDocuments:
        """Observe only metadata returned by the current access-filtered SQL predicate."""
        started = perf_counter()
        async with self.policy.protected_read(self.actor.token) as uow:
            items = await list_accessible_documents(uow)
        self.observations.append(
            Observation(
                action_id=action_id,
                surface="document_list",
                source_refs=(),
                forbidden_refs=(),
                provenance_valid=True,
                elapsed_ms=(perf_counter() - started) * 1000,
                titles=tuple(item.title for item in items),
            )
        )
        return AccessibleDocuments(items=items)

    async def source_read(self, action_id: str, ref: SourceRef) -> SourceContent | None:
        """Observe source content or indistinguishable denial through the real service."""
        started = perf_counter()
        async with self.policy.protected_read(self.actor.token) as uow:
            snapshot = await PostgresPolicyReader(uow).snapshot(self.actor.user_id, (ref,))
            try:
                source = (
                    await read_source(uow, ref)
                    if self.arm == "guarded"
                    else await baseline_source(uow, ref)
                )
            except SourceNotFound:
                source = None
        self.observations.append(
            Observation(
                action_id=action_id,
                surface="source_read",
                source_refs=(ref,) if source else (),
                forbidden_refs=snapshot.denied_refs if source else (),
                provenance_valid=snapshot.provenance_valid,
                elapsed_ms=(perf_counter() - started) * 1000,
                body=source.text if source else "",
                titles=(source.title,) if source else (),
            )
        )
        return source
