"""Uncommitted generation outcomes and transaction-bound release decisions."""

from dataclasses import dataclass, field

from rag_access_guard import Guard, TokenCounter
from rag_access_guard.context import matches_history
from rag_access_guard_api.adapters.policy import PostgresPolicyReader
from rag_access_guard_api.services.chat_repository import load_prior_turns
from rag_access_guard_api.services.chat_state import GenerationAttempt, NeutralReason, Reservation
from rag_access_guard_api.services.chat_turns import ReleasedAnswer
from rag_access_guard_api.services.security import ReadUoW


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


async def release_result(
    uow: ReadUoW, outcome: Generated | Neutral
) -> ReleasedAnswer | NeutralReason | StaleGeneration:
    """Decide within the caller's UoW; persistence and commit remain in ChatService."""
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
