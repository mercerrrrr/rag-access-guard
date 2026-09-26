from dataclasses import dataclass, field, replace
from hashlib import sha256
from typing import Literal
from uuid import UUID

import pytest
from packages.rag_access_guard.tests.test_history import Counter, Reader, ref

from rag_access_guard import (
    CandidateChunk,
    Guard,
    PolicySnapshot,
    PreparedContext,
    PrepareDenied,
    PriorTurn,
    SourceRef,
)

type ReaderMode = Literal[
    "allowed",
    "exception",
    "revision",
    "inactive",
    "principal",
    "unknown",
    "missing_hash",
    "coverage",
    "denied",
]


@dataclass(frozen=True, slots=True)
class ChangingReader:
    mode: ReaderMode
    calls: list[tuple[SourceRef, ...]] = field(default_factory=list)

    async def snapshot(
        self,
        principal_id: UUID,
        source_refs: tuple[SourceRef, ...],
        *,
        thread_id: UUID | None = None,
    ) -> PolicySnapshot:
        self.calls.append(source_refs)
        snapshot = await Reader().snapshot(principal_id, source_refs, thread_id=thread_id)
        if len(self.calls) == 1:
            return snapshot
        if self.mode == "exception":
            message = "PRIVATE_POLICY_ERROR"
            raise RuntimeError(message)
        variants = {
            "revision": replace(snapshot, revision=8),
            "inactive": replace(snapshot, principal_active=False),
            "principal": replace(snapshot, principal_id=UUID(int=999)),
            "unknown": replace(snapshot, provenance_valid=False),
            "missing_hash": replace(snapshot, canonical_chunk_hashes=()),
            "coverage": replace(snapshot, allowed_refs=()),
            "denied": replace(
                snapshot, allowed_refs=(), denied_refs=source_refs, canonical_chunk_hashes=()
            ),
        }
        return variants.get(self.mode, snapshot)


def turn() -> PriorTurn:
    return PriorTurn(
        turn_id=UUID(int=200),
        user_input="OLD_USER",
        answer="OLD_ANSWER",
        source_refs=(ref(2), ref(2)),
        provenance_complete=True,
    )


def chunk() -> CandidateChunk:
    return CandidateChunk(
        source_ref=ref(1),
        text="CURRENT_MARKER",
        content_sha256=sha256(b"CURRENT_MARKER").hexdigest(),
        token_count=1,
    )


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["exception", "revision", "inactive", "principal"])
async def test_incoherent_history_policy_denies_whole_preparation(mode: ReaderMode) -> None:
    result = await Guard(Counter()).prepare_context(
        UUID(int=1), (chunk(),), (turn(),), ChangingReader(mode)
    )
    assert isinstance(result, PrepareDenied)
    assert result.reason == (
        "denied" if mode in {"inactive", "principal"} else "policy_unavailable"
    )


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["unknown", "missing_hash", "coverage", "denied"])
async def test_invalid_pair_preserves_valid_current_chunks(mode: ReaderMode) -> None:
    result = await Guard(Counter()).prepare_context(
        UUID(int=1), (chunk(),), (turn(),), ChangingReader(mode)
    )
    assert isinstance(result, PreparedContext)
    assert result.source_refs == (ref(1),)
    assert "OLD_USER" not in result.model_context
    assert "OLD_ANSWER" not in result.model_context


@pytest.mark.anyio
async def test_each_pair_has_its_own_exact_policy_subset() -> None:
    reader = ChangingReader("allowed")
    first = replace(turn(), source_refs=(ref(2), ref(2)))
    second = replace(turn(), turn_id=UUID(int=201), source_refs=(ref(3), ref(2)))
    result = await Guard(Counter()).prepare_context(
        UUID(int=1), (chunk(),), (first, second), reader
    )
    assert isinstance(result, PreparedContext)
    assert reader.calls == [(ref(1),), (ref(2),), (ref(3), ref(2))]
    assert result.source_refs == (ref(1), ref(2), ref(3))


@pytest.mark.anyio
async def test_zero_history_limit_never_queries_pairs() -> None:
    reader = ChangingReader("exception")
    result = await Guard(Counter(), max_prior_turns=0).prepare_context(
        UUID(int=1), (chunk(),), (turn(),), reader
    )
    assert isinstance(result, PreparedContext)
    assert reader.calls == [(ref(1),)]
    assert "OLD_ANSWER" not in result.model_context
