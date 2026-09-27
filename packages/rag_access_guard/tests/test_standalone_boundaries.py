import asyncio
from dataclasses import replace
from hashlib import sha256
from uuid import UUID

import pytest
from scripts.demo_guard_host import (
    DEMO_DOCUMENT,
    DEMO_PRINCIPAL,
    DEMO_QUESTION,
    DEMO_SOURCE,
    DEMO_THREAD,
    OLD_SOURCE,
    OTHER_PRINCIPAL,
    OTHER_SOURCE,
    OTHER_THREAD,
    DemoHost,
    create_demo_host,
)

from rag_access_guard import CandidateChunk, PreparedContext, PrepareDenied


def test_guard_rejects_closed_text_with_allowed_ref_and_self_consistent_hash() -> None:
    async def scenario() -> None:
        host = create_demo_host()
        _ = await host.grant(DEMO_PRINCIPAL, DEMO_DOCUMENT)
        text = "SYNTHETIC_BETA_PRIVATE"
        forged = CandidateChunk(
            source_ref=DEMO_SOURCE,
            text=text,
            content_sha256=sha256(text.encode()).hexdigest(),
            token_count=len(text.encode()),
        )
        async with host.policy_scope(DEMO_PRINCIPAL, thread_id=DEMO_THREAD) as reader:
            result = await host.guard.prepare_context(DEMO_PRINCIPAL, (forged,), (), reader)
        assert isinstance(result, PrepareDenied)
        assert result.reason == "invalid_provenance"
        assert host.model_calls == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("version", [None, OLD_SOURCE.document_version_id])
def test_known_old_or_inactive_version_is_denied_with_valid_provenance(
    version: UUID | None,
) -> None:
    async def scenario() -> None:
        host = create_demo_host()
        _ = await host.grant(DEMO_PRINCIPAL, DEMO_DOCUMENT)
        _ = await host.set_version(DEMO_DOCUMENT, version)
        async with host.policy_scope(DEMO_PRINCIPAL) as reader:
            snapshot = await reader.snapshot(DEMO_PRINCIPAL, (DEMO_SOURCE,))
        assert snapshot.provenance_valid
        assert snapshot.denied_refs == (DEMO_SOURCE,)
        assert snapshot.canonical_chunk_hashes == ()

    asyncio.run(scenario())


def test_denied_candidates_and_hash_witness_have_exact_coverage() -> None:
    async def scenario() -> None:
        host = create_demo_host()
        _ = await host.grant(DEMO_PRINCIPAL, DEMO_DOCUMENT)
        async with host.policy_scope(DEMO_PRINCIPAL) as reader:
            snapshot = await reader.snapshot(
                DEMO_PRINCIPAL, (DEMO_SOURCE, OTHER_SOURCE, OLD_SOURCE)
            )
        assert snapshot.allowed_refs == (DEMO_SOURCE,)
        assert snapshot.denied_refs == (OTHER_SOURCE, OLD_SOURCE)
        assert tuple(ref for ref, _ in snapshot.canonical_chunk_hashes) == (DEMO_SOURCE,)
        prepared = await host.prepare(DEMO_PRINCIPAL, DEMO_THREAD)
        assert isinstance(prepared, PreparedContext)
        assert prepared.source_refs == (DEMO_SOURCE,)
        assert "SYNTHETIC_BETA_PRIVATE" not in prepared.model_context
        _ = await host.revoke(DEMO_PRINCIPAL, DEMO_DOCUMENT)
        assert snapshot.allowed_refs == (DEMO_SOURCE,)
        assert snapshot.revision == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["unknown", "mixed", "hash", "bytes"])
def test_snapshot_rejects_noncanonical_identity_or_storage(fault: str) -> None:
    async def scenario() -> None:
        host = create_demo_host()
        ref = DEMO_SOURCE
        if fault == "unknown":
            ref = replace(ref, chunk_id=UUID(int=99))
        elif fault == "mixed":
            ref = replace(ref, document_id=OTHER_SOURCE.document_id)
        else:
            row = host.canonical(ref)
            assert row is not None
            corrupt = (
                replace(row, content_sha256="0" * 64)
                if fault == "hash"
                else replace(row, original=b"CORRUPT")
            )
            host = DemoHost((corrupt,))
        _ = await host.grant(DEMO_PRINCIPAL, DEMO_DOCUMENT)
        async with host.policy_scope(DEMO_PRINCIPAL) as reader:
            snapshot = await reader.snapshot(DEMO_PRINCIPAL, (ref,))
        assert not snapshot.provenance_valid
        assert snapshot.allowed_refs == ()

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["inactive", "expired", "principal", "thread", "unknown"])
def test_session_and_thread_are_rechecked_at_each_gate(fault: str) -> None:
    async def scenario() -> None:
        host = create_demo_host()
        _ = await host.grant(DEMO_PRINCIPAL, DEMO_DOCUMENT)
        prepared = await host.prepare(DEMO_PRINCIPAL, DEMO_THREAD)
        assert isinstance(prepared, PreparedContext)
        allowed = await host.release(DEMO_PRINCIPAL, DEMO_THREAD, prepared, "SYNTHETIC_SAVED")
        assert allowed.allowed
        pending = await host.prepare(DEMO_PRINCIPAL, DEMO_THREAD)
        assert isinstance(pending, PreparedContext)
        principal, thread = DEMO_PRINCIPAL, DEMO_THREAD
        if fault == "inactive":
            _ = await host.set_session(active=False)
        elif fault == "expired":
            _ = await host.set_session(expired=True)
        elif fault == "principal":
            principal = OTHER_PRINCIPAL
        elif fault == "thread":
            thread = OTHER_THREAD
        else:
            thread = UUID(int=99)
        denied = await host.prepare(principal, thread)
        assert isinstance(denied, PrepareDenied)
        view = await host.read_saved(principal, thread)
        assert view.answer is None
        assert view.user_input == ""
        assert view.sources == ()
        decision = await host.release(principal, thread, pending, "SYNTHETIC_PENDING")
        assert not decision.allowed
        assert host.saved_count == 1

    asyncio.run(scenario())


def test_direct_grant_survives_role_revocation() -> None:
    async def scenario() -> None:
        host = create_demo_host()
        _ = await host.grant(DEMO_PRINCIPAL, DEMO_DOCUMENT)
        _ = await host.set_role(DEMO_PRINCIPAL, DEMO_DOCUMENT, member=True)
        _ = await host.set_role(DEMO_PRINCIPAL, DEMO_DOCUMENT, member=False)
        prepared = await host.prepare(DEMO_PRINCIPAL, DEMO_THREAD)
        assert isinstance(prepared, PreparedContext)
        assert prepared.source_refs == (DEMO_SOURCE,)
        assert prepared.policy_revision == 3

    asyncio.run(scenario())


def test_reader_cannot_snapshot_outside_owned_scope() -> None:
    async def scenario() -> None:
        host = create_demo_host()
        async with host.policy_scope(DEMO_PRINCIPAL) as reader:
            with pytest.raises(RuntimeError, match="inactive_policy_scope"):
                _ = await asyncio.create_task(reader.snapshot(DEMO_PRINCIPAL, (DEMO_SOURCE,)))
        with pytest.raises(RuntimeError, match="inactive_policy_scope"):
            _ = await reader.snapshot(DEMO_PRINCIPAL, (DEMO_SOURCE,))

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "field", ["model_context", "source_refs", "policy_revision", "fingerprint"]
)
def test_prepared_tampering_never_releases(field: str) -> None:
    async def scenario() -> None:
        host = create_demo_host()
        _ = await host.grant(DEMO_PRINCIPAL, DEMO_DOCUMENT)
        prepared = await host.prepare(DEMO_PRINCIPAL, DEMO_THREAD)
        assert isinstance(prepared, PreparedContext)
        altered = {
            "model_context": replace(prepared, model_context="forged"),
            "source_refs": replace(prepared, source_refs=(OTHER_SOURCE,)),
            "policy_revision": replace(prepared, policy_revision=999),
            "fingerprint": replace(prepared, fingerprint="0" * 64),
        }[field]
        decision = await host.release(DEMO_PRINCIPAL, DEMO_THREAD, altered, "SYNTHETIC_FORGED")
        assert not decision.allowed
        assert host.saved_count == 0

    asyncio.run(scenario())


def test_no_authorized_context_skips_model() -> None:
    async def scenario() -> None:
        host = create_demo_host()
        prepared = await host.prepare(DEMO_PRINCIPAL, DEMO_THREAD)
        assert isinstance(prepared, PreparedContext)
        assert not prepared.source_refs
        with pytest.raises(RuntimeError, match="invalid_model_call"):
            _ = host.model(DEMO_QUESTION, prepared)
        assert host.model_calls == 0

    asyncio.run(scenario())
