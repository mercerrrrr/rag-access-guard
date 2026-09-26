from dataclasses import replace
from uuid import UUID

import pytest
from packages.rag_access_guard.tests.test_authorize_read import FakePolicyReader, UnavailableReader
from packages.rag_access_guard.tests.test_prepare_release import Counter, candidate, reader

from rag_access_guard import Guard, PolicySnapshot, PreparedContext


def invalid_snapshots() -> tuple[PolicySnapshot, ...]:
    snapshot = reader().value
    ref = candidate().source_ref
    other = replace(ref, document_id=UUID(int=99))
    return (
        replace(snapshot, allowed_refs=()),
        replace(snapshot, allowed_refs=(ref, other)),
        replace(snapshot, allowed_refs=(ref, ref)),
        replace(snapshot, denied_refs=(ref,)),
        replace(snapshot, allowed_refs=(), denied_refs=(ref, ref), canonical_chunk_hashes=()),
        replace(snapshot, canonical_chunk_hashes=()),
        replace(
            snapshot,
            canonical_chunk_hashes=(
                *snapshot.canonical_chunk_hashes,
                *snapshot.canonical_chunk_hashes,
            ),
        ),
        replace(snapshot, canonical_chunk_hashes=((other, "a" * 64),)),
        replace(snapshot, canonical_chunk_hashes=((ref, "g" * 64),)),
        replace(snapshot, canonical_chunk_hashes=((ref, "a" * 63),)),
        replace(snapshot, canonical_chunk_hashes=((ref, "A" * 64),)),
        replace(snapshot, provenance_valid=False),
    )


@pytest.mark.anyio
@pytest.mark.parametrize("snapshot", invalid_snapshots())
async def test_read_and_release_share_exact_snapshot_partition(snapshot: PolicySnapshot) -> None:
    guard = Guard(Counter())
    prepared = await guard.prepare_context(UUID(int=1), (candidate(),), (), reader())
    assert isinstance(prepared, PreparedContext)
    policy = FakePolicyReader(snapshot)
    read = await guard.authorize_read(UUID(int=1), prepared.source_refs, policy)
    release = await guard.authorize_release(UUID(int=1), UUID(int=5), prepared, policy)
    assert not read.allowed
    assert read.reason == "invalid_provenance"
    assert not release.allowed
    assert release.reason == "invalid_provenance"
    assert read.policy_revision == release.policy_revision == snapshot.revision


@pytest.mark.anyio
@pytest.mark.parametrize("fault", ["principal", "inactive", "foreign_thread", "unknown_owner"])
async def test_binding_denial_precedes_revision_conflict(fault: str) -> None:
    guard = Guard(Counter())
    prepared = await guard.prepare_context(UUID(int=1), (candidate(),), (), reader())
    assert isinstance(prepared, PreparedContext)
    snapshot = replace(reader().value, revision=8)
    if fault == "principal":
        snapshot = replace(snapshot, principal_id=UUID(int=99))
    elif fault == "inactive":
        snapshot = replace(snapshot, principal_active=False)
    else:
        snapshot = replace(snapshot, thread_owned=False if fault == "foreign_thread" else None)
    release = await guard.authorize_release(
        UUID(int=1), UUID(int=5), prepared, FakePolicyReader(snapshot)
    )
    assert release.reason == "denied"
    assert not release.allowed


@pytest.mark.anyio
async def test_release_policy_exception_has_no_details(capsys: pytest.CaptureFixture[str]) -> None:
    guard = Guard(Counter())
    prepared = await guard.prepare_context(UUID(int=1), (candidate(),), (), reader())
    assert isinstance(prepared, PreparedContext)
    release = await guard.authorize_release(UUID(int=1), UUID(int=5), prepared, UnavailableReader())
    assert release.reason == "policy_unavailable"
    assert release.policy_revision is None
    assert "synthetic-private-policy-detail" not in repr(release)
    assert capsys.readouterr() == ("", "")


def test_negative_snapshot_revision_is_rejected_at_boundary() -> None:
    with pytest.raises(ValueError, match="nonnegative"):
        _ = replace(reader().value, revision=-1)


@pytest.mark.anyio
async def test_valid_denied_snapshot_does_not_invalidate_provenance() -> None:
    guard = Guard(Counter())
    prepared = await guard.prepare_context(UUID(int=1), (candidate(),), (), reader())
    assert isinstance(prepared, PreparedContext)
    policy = FakePolicyReader(
        replace(
            reader().value,
            allowed_refs=(),
            denied_refs=prepared.source_refs,
            canonical_chunk_hashes=(),
        )
    )
    read = await guard.authorize_read(UUID(int=1), prepared.source_refs, policy)
    release = await guard.authorize_release(UUID(int=1), UUID(int=5), prepared, policy)
    assert read.reason == release.reason == "denied"
    assert not read.allowed
    assert not release.allowed
