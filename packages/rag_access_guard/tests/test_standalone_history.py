import asyncio
from dataclasses import dataclass, replace
from uuid import UUID

import pytest
from scripts.demo_guard_host import (
    DEMO_DOCUMENT,
    DEMO_PRINCIPAL,
    DEMO_SOURCE,
    DEMO_THREAD,
    OTHER_SOURCE,
    create_demo_host,
)

from rag_access_guard import (
    PolicyReader,
    PolicySnapshot,
    PreparedContext,
    PrepareDenied,
    PriorTurn,
    SourceRef,
)


@dataclass
class FaultReader:
    delegate: PolicyReader
    fault: str
    calls: int = 0

    async def snapshot(
        self,
        principal_id: UUID,
        source_refs: tuple[SourceRef, ...],
        *,
        thread_id: UUID | None = None,
    ) -> PolicySnapshot:
        self.calls += 1
        snapshot = await self.delegate.snapshot(principal_id, source_refs, thread_id=thread_id)
        if self.calls == 2:
            if self.fault == "revision":
                return replace(snapshot, revision=snapshot.revision + 1)
            message = "SYNTHETIC_POLICY_EXCEPTION"
            raise RuntimeError(message)
        return snapshot


@pytest.mark.parametrize("fault", ["revision", "exception"])
def test_history_policy_fault_discards_all_prepared_context(fault: str) -> None:
    async def scenario() -> None:
        host = create_demo_host()
        _ = await host.grant(DEMO_PRINCIPAL, DEMO_DOCUMENT)
        row = host.canonical(DEMO_SOURCE)
        assert row is not None
        prior = PriorTurn(
            turn_id=UUID(int=99),
            user_input="HISTORICAL_QUESTION",
            answer="HISTORICAL_SECRET",
            source_refs=(DEMO_SOURCE,),
            provenance_complete=True,
        )
        async with host.policy_scope(DEMO_PRINCIPAL) as reader:
            result = await host.guard.prepare_context(
                DEMO_PRINCIPAL, (row.candidate(),), (prior,), FaultReader(reader, fault)
            )
        assert isinstance(result, PrepareDenied)
        assert result.reason == "policy_unavailable"
        assert "HISTORICAL" not in repr(result)
        assert "SYNTHETIC_POLICY_EXCEPTION" not in repr(result)
        assert host.model_calls == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["denied", "incomplete", "unknown"])
def test_invalid_history_pair_does_not_poison_valid_candidates(fault: str) -> None:
    async def scenario() -> None:
        host = create_demo_host()
        _ = await host.grant(DEMO_PRINCIPAL, DEMO_DOCUMENT)
        row = host.canonical(DEMO_SOURCE)
        assert row is not None
        ref = replace(OTHER_SOURCE, chunk_id=UUID(int=99)) if fault == "unknown" else OTHER_SOURCE
        prior = PriorTurn(
            turn_id=UUID(int=100),
            user_input="REMOVED_QUESTION",
            answer="REMOVED_ANSWER",
            source_refs=(DEMO_SOURCE, ref),
            provenance_complete=fault != "incomplete",
        )
        good = PriorTurn(
            turn_id=UUID(int=101),
            user_input="VALID_QUESTION",
            answer="VALID_ANSWER",
            source_refs=(DEMO_SOURCE,),
            provenance_complete=True,
        )
        async with host.policy_scope(DEMO_PRINCIPAL) as reader:
            result = await host.guard.prepare_context(
                DEMO_PRINCIPAL, (row.candidate(),), (prior, good), reader
            )
        assert isinstance(result, PreparedContext)
        assert "REMOVED" not in result.model_context
        assert "VALID_ANSWER" in result.model_context
        assert result.source_refs == (DEMO_SOURCE,)

    asyncio.run(scenario())


def test_saved_closure_survives_missing_visible_citation() -> None:
    async def scenario() -> None:
        host = create_demo_host()
        _ = await host.grant(DEMO_PRINCIPAL, DEMO_DOCUMENT)
        _ = await host.grant(DEMO_PRINCIPAL, OTHER_SOURCE.document_id)
        prepared = await host.prepare(DEMO_PRINCIPAL, DEMO_THREAD)
        assert isinstance(prepared, PreparedContext)
        assert set(prepared.source_refs) == {DEMO_SOURCE, OTHER_SOURCE}
        released = await host.release(
            DEMO_PRINCIPAL, DEMO_THREAD, prepared, "Answer without citations"
        )
        assert released.allowed
        _ = await host.revoke(DEMO_PRINCIPAL, OTHER_SOURCE.document_id)
        view = await host.read_saved(DEMO_PRINCIPAL, DEMO_THREAD)
        assert view.answer is None
        assert view.sources == ()
        assert host.saved_count == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("revoke_first", [True, False])
def test_memory_release_revocation_ordering(
    caplog: pytest.LogCaptureFixture, *, revoke_first: bool
) -> None:
    async def scenario() -> None:
        host = create_demo_host()
        _ = await host.grant(DEMO_PRINCIPAL, DEMO_DOCUMENT)
        prepared = await host.prepare(DEMO_PRINCIPAL, DEMO_THREAD)
        assert isinstance(prepared, PreparedContext)
        first_committed = asyncio.Event()

        async def revoke() -> None:
            if not revoke_first:
                _ = await first_committed.wait()
            _ = await host.revoke(DEMO_PRINCIPAL, DEMO_DOCUMENT)
            first_committed.set()

        async def release() -> None:
            if revoke_first:
                _ = await first_committed.wait()
            result = await host.release(DEMO_PRINCIPAL, DEMO_THREAD, prepared, "SYNTHETIC_PENDING")
            assert result.allowed is not revoke_first
            if revoke_first:
                assert result.reason == "stale_revision"
            first_committed.set()

        _ = await asyncio.gather(revoke(), release())
        assert host.saved_count == (0 if revoke_first else 1)
        view = await host.read_saved(DEMO_PRINCIPAL, DEMO_THREAD)
        assert view.answer is None
        assert view.sources == ()
        assert "SYNTHETIC_PENDING" not in repr(view)

    asyncio.run(scenario())
    assert "SYNTHETIC_PENDING" not in caplog.text
