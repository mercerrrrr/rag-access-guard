"""Transactional answer release for the isolated experimental host."""

from __future__ import annotations

from time import perf_counter
from typing import TYPE_CHECKING

from experiments.attempts import bound_turn, lease_active
from experiments.baseline import baseline_integrity
from experiments.baseline_reads import baseline_answer
from experiments.input_boundary import observe_boundary
from experiments.observations import Observation
from rag_access_guard import Guard
from rag_access_guard.context import matches_history
from rag_access_guard_api.adapters.policy import PostgresPolicyReader
from rag_access_guard_api.schemas.chat import MessageResponse
from rag_access_guard_api.services.chat_read import read_turn
from rag_access_guard_api.services.chat_repository import load_prior_turns
from rag_access_guard_api.services.chat_turns import ReleasedAnswer, complete_turn, load_turns
from rag_access_guard_api.services.security import revalidate_session

if TYPE_CHECKING:
    from experiments.host_state import HostState, PendingAnswer


async def finish_answer(
    host: HostState,
    pending: PendingAnswer,
) -> MessageResponse | None:
    """Commit answer and closure only inside a fresh session/thread/policy gate."""
    reservation, prepared, body = pending.reservation, pending.prepared, pending.body
    action_id, request, started, retry = (
        pending.action_id,
        pending.request,
        pending.started,
        pending.retry,
    )
    async with host.policy.protected_read(host.actor.token) as uow:
        turn = await bound_turn(uow, host.actor, reservation, request)
        active = await lease_active(uow, turn)
        snapshot = await PostgresPolicyReader(uow).snapshot(
            host.actor.user_id, prepared.source_refs, thread_id=reservation.thread_id
        )
        release = None
        if host.arm == "guarded" and body and active:
            release = await Guard(
                host.config.token_counter(),
                host.config.max_context_tokens,
                host.config.max_prior_turns,
            ).authorize_release(
                host.actor.user_id, reservation.thread_id, prepared, PostgresPolicyReader(uow)
            )
        integrity = (
            await baseline_integrity(
                uow, prepared, await load_prior_turns(uow, reservation.thread_id), host.config
            )
            if host.arm == "baseline" and body
            else False
        )
        allowed = bool(
            active
            and body
            and prepared.source_refs
            and (
                release is not None
                and release.allowed
                and matches_history(prepared, await load_prior_turns(uow, reservation.thread_id))
                if host.arm == "guarded"
                else integrity
            )
        )
        await revalidate_session(uow, host.actor.token)
        active = active and await lease_active(uow, turn)
        if active and release is not None and release.reason == "stale_revision" and retry:
            return None
        allowed = allowed and active
        host.revision = await complete_turn(
            uow,
            turn,
            ReleasedAnswer(body, prepared.source_refs)
            if allowed
            else "no_context"
            if active
            else "interrupted",
        )
        completed = next(t for t in await load_turns(uow, reservation.thread_id) if t.id == turn.id)
        view = (
            await baseline_answer(uow, completed)
            if host.arm == "baseline" and completed.state == "available"
            else await read_turn(uow, completed)
        )
        response = MessageResponse(thread_revision=host.revision, turn=view, replayed=False)
    refs = prepared.source_refs if allowed else ()
    if allowed and pending.captured is not None:
        host.origins[completed.id] = pending.captured.user_origin_markers
    if host.ordering is not None:
        host.ordering.commits.append(action_id)
        await host.ordering.boundary(action_id, "release_committed")
    host.observations.append(
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
                for marker in sorted(host.synthetic_markers)
            )
            if pending.captured
            else (),
        )
    )
    return response
