import asyncio
from dataclasses import dataclass, replace
from uuid import UUID

import pytest
from scripts.demo_guard_host import (
    DEMO_DOCUMENT,
    DEMO_PRINCIPAL,
    DEMO_SOURCE,
    OTHER_SOURCE,
    create_demo_host,
)

from rag_access_guard import PolicyReader, PolicySnapshot, PrepareDenied, SourceRef


@dataclass
class FaultWitness:
    reader: PolicyReader
    fault: str

    async def snapshot(
        self,
        principal_id: UUID,
        source_refs: tuple[SourceRef, ...],
        *,
        thread_id: UUID | None = None,
    ) -> PolicySnapshot:
        if self.fault == "exception":
            message = "SYNTHETIC_POLICY_EXCEPTION"
            raise RuntimeError(message)
        original = await self.reader.snapshot(principal_id, source_refs, thread_id=thread_id)
        witness = original.canonical_chunk_hashes
        changes = {
            "missing": (),
            "duplicate": witness + witness,
            "denied": (*witness, (OTHER_SOURCE, "0" * 64)),
            "malformed": ((DEMO_SOURCE, "invalid"),),
            "foreign_hash": ((DEMO_SOURCE, "0" * 64),),
        }
        if self.fault == "extra_ref":
            return replace(original, allowed_refs=(*original.allowed_refs, OTHER_SOURCE))
        if self.fault == "missing_ref":
            return replace(original, allowed_refs=())
        if self.fault == "conflict":
            return replace(original, denied_refs=original.allowed_refs)
        return replace(original, canonical_chunk_hashes=changes[self.fault])


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "duplicate",
        "denied",
        "malformed",
        "foreign_hash",
        "extra_ref",
        "missing_ref",
        "conflict",
        "exception",
    ],
)
def test_canonical_reader_fault_never_returns_partial_context(fault: str) -> None:
    async def scenario() -> None:
        host = create_demo_host()
        _ = await host.grant(DEMO_PRINCIPAL, DEMO_DOCUMENT)
        row = host.canonical(DEMO_SOURCE)
        assert row is not None
        async with host.policy_scope(DEMO_PRINCIPAL) as reader:
            result = await host.guard.prepare_context(
                DEMO_PRINCIPAL, (row.candidate(),), (), FaultWitness(reader, fault)
            )
        assert isinstance(result, PrepareDenied)
        assert result.reason == (
            "policy_unavailable" if fault == "exception" else "invalid_provenance"
        )
        assert "SYNTHETIC" not in repr(result)
        assert host.model_calls == 0

    asyncio.run(scenario())
