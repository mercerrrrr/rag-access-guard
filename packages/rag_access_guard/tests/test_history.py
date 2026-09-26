from dataclasses import dataclass, replace
from hashlib import sha256
from uuid import UUID

import pytest

from rag_access_guard import (
    CandidateChunk,
    Guard,
    PolicySnapshot,
    PreparedContext,
    PriorTurn,
    SourceRef,
)


@dataclass(frozen=True, slots=True)
class Counter:
    identity: str = "characters-v1"

    def count(self, text: str) -> int:
        return len(text)


def ref(number: int) -> SourceRef:
    return SourceRef(
        document_id=UUID(int=number),
        document_version_id=UUID(int=number + 10),
        chunk_id=UUID(int=number + 20),
    )


@dataclass(frozen=True, slots=True)
class Reader:
    denied: tuple[SourceRef, ...] = ()

    async def snapshot(
        self,
        principal_id: UUID,
        source_refs: tuple[SourceRef, ...],
        *,
        thread_id: UUID | None = None,
    ) -> PolicySnapshot:
        allowed = tuple(r for r in source_refs if r not in self.denied)
        return PolicySnapshot(
            principal_id=principal_id,
            revision=7,
            allowed_refs=allowed,
            denied_refs=tuple(r for r in source_refs if r in self.denied),
            provenance_valid=True,
            principal_active=True,
            thread_owned=True if thread_id else None,
            canonical_chunk_hashes=tuple(
                (r, sha256(b"CURRENT_MARKER").hexdigest()) for r in allowed
            ),
        )


@dataclass(frozen=True, slots=True)
class HistoryCase:
    principal_id: UUID
    guard: Guard
    reader: Reader
    allowed_chunk: CandidateChunk
    mixed_turn: PriorTurn


@pytest.fixture
def history_case() -> HistoryCase:
    chunk = CandidateChunk(
        source_ref=ref(1),
        text="CURRENT_MARKER",
        content_sha256=sha256(b"CURRENT_MARKER").hexdigest(),
        token_count=1,
    )
    turn = PriorTurn(
        turn_id=UUID(int=200),
        user_input="HISTORICAL_QUESTION",
        answer="HISTORICAL_ANSWER",
        source_refs=(ref(1), ref(2)),
        provenance_complete=True,
    )

    return HistoryCase(UUID(int=100), Guard(Counter()), Reader(denied=(ref(2),)), chunk, turn)


@pytest.mark.anyio
async def test_one_denied_ref_removes_whole_history_pair(history_case: HistoryCase) -> None:
    prepared = await history_case.guard.prepare_context(
        history_case.principal_id,
        (history_case.allowed_chunk,),
        (history_case.mixed_turn,),
        history_case.reader,
    )
    assert isinstance(prepared, PreparedContext)
    assert history_case.mixed_turn.user_input not in prepared.model_context
    assert history_case.mixed_turn.answer not in prepared.model_context
    assert prepared.source_refs == (history_case.allowed_chunk.source_ref,)


@pytest.mark.anyio
async def test_allowed_history_keeps_roles_and_transitive_refs(history_case: HistoryCase) -> None:
    turn = history_case.mixed_turn
    prepared = await history_case.guard.prepare_context(
        history_case.principal_id, (history_case.allowed_chunk,), (turn,), Reader()
    )
    assert isinstance(prepared, PreparedContext)
    assert '"user_input":"HISTORICAL_QUESTION"' in prepared.model_context
    assert '"answer":"HISTORICAL_ANSWER"' in prepared.model_context
    assert prepared.source_refs == (ref(1), ref(2))
    assert (
        await history_case.guard.authorize_release(
            history_case.principal_id, UUID(int=300), prepared, Reader()
        )
    ).allowed


@pytest.mark.anyio
@pytest.mark.parametrize("count", [0, 1, 4, 5])
async def test_history_window_is_last_four_without_refill(
    history_case: HistoryCase, count: int
) -> None:
    turns = tuple(
        replace(
            history_case.mixed_turn,
            turn_id=UUID(int=200 + i),
            user_input=f"QUESTION_{i}",
            answer=f"ANSWER_{i}",
            source_refs=(ref(2) if i == count - 1 else ref(1),),
        )
        for i in range(count)
    )
    prepared = await history_case.guard.prepare_context(
        history_case.principal_id, (history_case.allowed_chunk,), turns, history_case.reader
    )
    assert isinstance(prepared, PreparedContext)
    expected = list(range(max(0, count - 4), max(0, count - 1)))
    for i in range(count):
        assert (f"QUESTION_{i}" in prepared.model_context) == (i in expected)
        assert (f"ANSWER_{i}" in prepared.model_context) == (i in expected)
    offsets = [prepared.model_context.index(f"QUESTION_{i}") for i in expected]
    assert offsets == sorted(offsets)


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["incomplete", "empty", "blank"])
async def test_unusable_history_does_not_discard_valid_chunks(
    history_case: HistoryCase, mode: str
) -> None:
    turn = replace(
        history_case.mixed_turn, source_refs=(ref(1),), provenance_complete=mode != "incomplete"
    )
    if mode == "empty":
        turn = replace(turn, source_refs=())
    if mode == "blank":
        turn = replace(turn, answer=" ")
    prepared = await history_case.guard.prepare_context(
        history_case.principal_id, (history_case.allowed_chunk,), (turn,), Reader()
    )
    assert isinstance(prepared, PreparedContext)
    assert turn.user_input not in prepared.model_context
    assert prepared.source_refs == (ref(1),)


@pytest.mark.anyio
async def test_budget_drops_whole_oldest_pair_and_its_closure(history_case: HistoryCase) -> None:
    turn = replace(history_case.mixed_turn, answer="X" * 5000)
    prepared = await history_case.guard.prepare_context(
        history_case.principal_id, (history_case.allowed_chunk,), (turn,), Reader()
    )
    assert isinstance(prepared, PreparedContext)
    assert turn.user_input not in prepared.model_context
    assert prepared.source_refs == (ref(1),)
