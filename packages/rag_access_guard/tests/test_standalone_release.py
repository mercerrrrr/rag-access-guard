import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import UUID

import pytest
from scripts.demo_guard_host import (
    DEMO_DOCUMENT,
    DEMO_PRINCIPAL,
    DEMO_THREAD,
    DemoHost,
    MemoryPolicyReader,
    create_demo_host,
)

from rag_access_guard import Guard, PolicyReader, PreparedContext, ReleaseDecision


def test_equal_pending_contexts_are_consumed_by_identity_once() -> None:
    async def scenario() -> None:
        host = create_demo_host()
        _ = await host.grant(DEMO_PRINCIPAL, DEMO_DOCUMENT)
        first = await host.prepare(DEMO_PRINCIPAL, DEMO_THREAD)
        second = await host.prepare(DEMO_PRINCIPAL, DEMO_THREAD)
        assert isinstance(first, PreparedContext)
        assert isinstance(second, PreparedContext)
        assert first == second
        assert first is not second
        decision = await host.release(DEMO_PRINCIPAL, DEMO_THREAD, second, "SECOND")
        assert decision.allowed
        replay = await host.release(DEMO_PRINCIPAL, DEMO_THREAD, second, "REPLAY")
        assert not replay.allowed
        assert host.saved_count == 1
        remaining = await host.release(DEMO_PRINCIPAL, DEMO_THREAD, first, "FIRST")
        assert remaining.allowed
        assert host.saved_count == 2

    asyncio.run(scenario())


def test_revoke_waits_through_authorization_and_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        host = create_demo_host()
        _ = await host.grant(DEMO_PRINCIPAL, DEMO_DOCUMENT)
        prepared = await host.prepare(DEMO_PRINCIPAL, DEMO_THREAD)
        assert isinstance(prepared, PreparedContext)
        authorized = asyncio.Event()
        continue_release = asyncio.Event()
        revoke_attempted = asyncio.Event()
        revoke_committed = asyncio.Event()
        original_gate = Guard.authorize_release
        original_scope = DemoHost.policy_scope
        releasing: asyncio.Task[ReleaseDecision] | None = None

        async def observed_gate(
            self: Guard,
            principal_id: UUID,
            thread_id: UUID,
            context: PreparedContext,
            reader: PolicyReader,
        ) -> ReleaseDecision:
            decision = await original_gate(self, principal_id, thread_id, context, reader)
            assert decision.allowed
            authorized.set()
            _ = await continue_release.wait()
            return decision

        @asynccontextmanager
        async def observed_scope(
            self: DemoHost, principal_id: UUID, *, thread_id: UUID | None = None
        ) -> AsyncGenerator[MemoryPolicyReader]:
            async with original_scope(self, principal_id, thread_id=thread_id) as reader:
                yield reader
                if asyncio.current_task() is releasing:
                    assert self.saved_count == 1
                    assert not revoke_committed.is_set()

        async def revoke() -> None:
            revoke_attempted.set()
            _ = await host.revoke(DEMO_PRINCIPAL, DEMO_DOCUMENT)
            revoke_committed.set()

        monkeypatch.setattr(Guard, "authorize_release", observed_gate)
        monkeypatch.setattr(DemoHost, "policy_scope", observed_scope)
        releasing = asyncio.create_task(
            host.release(DEMO_PRINCIPAL, DEMO_THREAD, prepared, "PENDING")
        )
        _ = await authorized.wait()
        revoking = asyncio.create_task(revoke())
        _ = await revoke_attempted.wait()
        assert not revoke_committed.is_set()
        assert host.saved_count == 0
        continue_release.set()
        released = await releasing
        await revoking
        assert released.allowed
        assert revoke_committed.is_set()
        assert host.saved_count == 1
        view = await host.read_saved(DEMO_PRINCIPAL, DEMO_THREAD)
        assert view.answer is None
        assert view.sources == ()

    asyncio.run(scenario())
