from dataclasses import dataclass, replace
from uuid import UUID

import pytest
from packages.rag_access_guard.tests.test_authorize_read import FakePolicyReader, UnavailableReader
from packages.rag_access_guard.tests.test_prepare_release import Counter, candidate, reader

from rag_access_guard import Guard, PreparedContext
from rag_access_guard.fingerprint import fingerprint


@dataclass(frozen=True, slots=True)
class ReleaseCase:
    guard: Guard
    prepared: PreparedContext
    policy: FakePolicyReader
    principal: UUID
    thread: UUID


@pytest.fixture
async def release_case() -> ReleaseCase:
    guard = Guard(Counter())
    policy = FakePolicyReader(reader().value)
    prepared = await guard.prepare_context(UUID(int=1), (candidate(),), (), policy)
    assert isinstance(prepared, PreparedContext)
    policy.calls.clear()
    return ReleaseCase(guard, prepared, policy, UUID(int=1), UUID(int=5))


@pytest.mark.anyio
async def test_release_denies_snapshot_with_unrequested_reference(
    release_case: ReleaseCase,
) -> None:
    case = release_case
    foreign = replace(candidate().source_ref, chunk_id=UUID(int=99))
    case.policy.value = replace(
        case.policy.value, allowed_refs=(*case.policy.value.allowed_refs, foreign)
    )
    result = await case.guard.authorize_release(
        case.principal, case.thread, case.prepared, case.policy
    )
    assert not result.allowed
    assert result.reason == "invalid_provenance"


@pytest.mark.anyio
@pytest.mark.parametrize("fault", ["fingerprint", "text", "budget", "identity"])
async def test_invalid_local_binding_precedes_policy_io(
    release_case: ReleaseCase, fault: str
) -> None:
    case = release_case
    prepared, guard = case.prepared, case.guard
    if fault == "fingerprint":
        prepared = replace(prepared, fingerprint="a" * 64)
    elif fault == "text":
        prepared = replace(prepared, model_context=prepared.model_context + " ")
    elif fault == "budget":
        guard = replace(guard, max_context_tokens=0)
    else:
        guard = replace(guard, token_counter=Counter(identity="different"))
    result = await guard.authorize_release(
        case.principal, case.thread, prepared, UnavailableReader()
    )
    assert result.reason == "invalid_provenance"
    assert result.policy_revision is None


@pytest.mark.anyio
@pytest.mark.parametrize("fault", ["unknown", "missing", "extra", "hash"])
async def test_invalid_snapshot_precedes_stale_revision(
    release_case: ReleaseCase, fault: str
) -> None:
    case = release_case
    snapshot = replace(case.policy.value, revision=8)
    if fault == "unknown":
        snapshot = replace(snapshot, provenance_valid=False)
    elif fault == "missing":
        snapshot = replace(snapshot, allowed_refs=(), canonical_chunk_hashes=())
    elif fault == "extra":
        snapshot = replace(
            snapshot, denied_refs=(replace(candidate().source_ref, chunk_id=UUID(int=99)),)
        )
    else:
        snapshot = replace(snapshot, canonical_chunk_hashes=((candidate().source_ref, "INVALID"),))
    case.policy.value = snapshot
    result = await case.guard.authorize_release(
        case.principal, case.thread, case.prepared, case.policy
    )
    assert not result.allowed
    assert result.reason == "invalid_provenance"
    assert result.policy_revision == 8


@pytest.mark.anyio
async def test_valid_revoked_snapshot_with_new_revision_is_stale(release_case: ReleaseCase) -> None:
    case = release_case
    case.policy.value = replace(
        case.policy.value,
        revision=8,
        allowed_refs=(),
        denied_refs=case.prepared.source_refs,
        canonical_chunk_hashes=(),
    )
    result = await case.guard.authorize_release(
        case.principal, case.thread, case.prepared, case.policy
    )
    assert not result.allowed
    assert result.reason == "stale_revision"
    assert result.policy_revision == 8


@pytest.mark.anyio
@pytest.mark.parametrize(
    "context", ["{}", '{"chunks":[],"version":true}', '{"chunks":[],"version":1}']
)
async def test_rehashed_invalid_structure_is_not_retryable(
    release_case: ReleaseCase, context: str
) -> None:
    case = release_case
    forged = replace(case.prepared, model_context=context)
    forged = replace(
        forged, fingerprint=fingerprint(forged, Counter().identity, 5000, max_prior_turns=4)
    )
    case.policy.value = replace(case.policy.value, revision=8)
    result = await case.guard.authorize_release(case.principal, case.thread, forged, case.policy)
    assert result.reason == "invalid_provenance"
    assert result.policy_revision is None
    assert case.policy.calls == []
