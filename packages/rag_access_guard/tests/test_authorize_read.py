from dataclasses import dataclass, replace
from typing import Final
from uuid import UUID

import pytest

from rag_access_guard import Guard, PolicySnapshot, SourceRef

PRINCIPAL: Final = UUID(int=1)
REF: Final = SourceRef(
    document_id=UUID(int=2), document_version_id=UUID(int=3), chunk_id=UUID(int=4)
)


@dataclass(frozen=True, slots=True)
class CharacterCounter:
    identity: str = "test-characters-v1"

    def count(self, text: str) -> int:
        return len(text)


class FakePolicyReader:
    """Return explicitly configured policy and record the actual query."""

    def __init__(self, value: PolicySnapshot) -> None:
        self.value: PolicySnapshot = value
        self.calls: list[tuple[UUID, tuple[SourceRef, ...], UUID | None]] = []

    async def snapshot(
        self,
        principal_id: UUID,
        source_refs: tuple[SourceRef, ...],
        *,
        thread_id: UUID | None = None,
    ) -> PolicySnapshot:
        self.calls.append((principal_id, source_refs, thread_id))
        return self.value


@pytest.mark.anyio
async def test_conflicting_snapshot_is_invalid_provenance() -> None:
    # Given: the adapter contradicts itself for the same source identity.
    reader = FakePolicyReader(
        PolicySnapshot(
            principal_id=PRINCIPAL,
            revision=7,
            allowed_refs=(REF,),
            denied_refs=(REF,),
            provenance_valid=True,
            principal_active=True,
            thread_owned=None,
            canonical_chunk_hashes=((REF, "a" * 64),),
        )
    )
    # When: the host asks to read that source.
    decision = await Guard(CharacterCounter()).authorize_read(PRINCIPAL, (REF,), reader)
    # Then: a conflicting policy never grants access.
    assert not decision.allowed
    assert decision.reason == "invalid_provenance"


def allowed_snapshot() -> PolicySnapshot:
    return PolicySnapshot(
        principal_id=PRINCIPAL,
        revision=7,
        allowed_refs=(REF,),
        denied_refs=(),
        provenance_valid=True,
        principal_active=True,
        thread_owned=None,
        canonical_chunk_hashes=((REF, "a" * 64),),
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("snapshot", "reason"),
    [
        (allowed_snapshot(), "allowed"),
        (
            replace(
                allowed_snapshot(), allowed_refs=(), denied_refs=(REF,), canonical_chunk_hashes=()
            ),
            "denied",
        ),
        (replace(allowed_snapshot(), principal_active=False), "denied"),
        (replace(allowed_snapshot(), principal_id=UUID(int=9)), "denied"),
        (replace(allowed_snapshot(), provenance_valid=False), "invalid_provenance"),
        (replace(allowed_snapshot(), allowed_refs=()), "invalid_provenance"),
        (replace(allowed_snapshot(), allowed_refs=(REF, REF)), "invalid_provenance"),
        (replace(allowed_snapshot(), canonical_chunk_hashes=()), "invalid_provenance"),
        (
            replace(allowed_snapshot(), canonical_chunk_hashes=((REF, "a" * 64), (REF, "a" * 64))),
            "invalid_provenance",
        ),
        (
            replace(allowed_snapshot(), canonical_chunk_hashes=((REF, "A" * 64),)),
            "invalid_provenance",
        ),
    ],
)
async def test_snapshot_decisions(snapshot: PolicySnapshot, reason: str) -> None:
    # Given: the host supplies an explicit policy snapshot.
    reader = FakePolicyReader(snapshot)
    # When: a read is authorized.
    decision = await Guard(CharacterCounter()).authorize_read(PRINCIPAL, (REF,), reader)
    # Then: only a complete, consistent authorization permits the read.
    assert decision.reason == reason
    assert decision.allowed == (reason == "allowed")
    assert reader.calls == [(PRINCIPAL, (REF,), None)]


@pytest.mark.anyio
async def test_empty_refs_are_invalid_provenance() -> None:
    reader = FakePolicyReader(allowed_snapshot())
    decision = await Guard(CharacterCounter()).authorize_read(PRINCIPAL, (), reader)
    assert decision.reason == "invalid_provenance"
    assert decision.policy_revision is None
    assert reader.calls == []


@pytest.mark.anyio
async def test_duplicate_input_refs_do_not_expand_access() -> None:
    reader = FakePolicyReader(allowed_snapshot())
    decision = await Guard(CharacterCounter()).authorize_read(PRINCIPAL, (REF, REF), reader)
    assert decision.allowed
    assert reader.calls == [(PRINCIPAL, (REF,), None)]


class UnavailableReader:
    async def snapshot(
        self,
        principal_id: UUID,
        source_refs: tuple[SourceRef, ...],
        *,
        thread_id: UUID | None = None,
    ) -> PolicySnapshot:
        _ = principal_id, source_refs, thread_id
        msg = "synthetic-private-policy-detail"
        raise RuntimeError(msg)


@pytest.mark.anyio
async def test_policy_exception_is_sanitized(capsys: pytest.CaptureFixture[str]) -> None:
    decision = await Guard(CharacterCounter()).authorize_read(
        PRINCIPAL, (REF,), UnavailableReader()
    )
    assert not decision.allowed
    assert decision.reason == "policy_unavailable"
    assert decision.policy_revision is None
    assert "synthetic-private-policy-detail" not in repr(decision)
    assert capsys.readouterr() == ("", "")


OTHER: Final = replace(REF, chunk_id=UUID(int=8))


@pytest.mark.anyio
@pytest.mark.parametrize(
    "snapshot",
    [
        replace(allowed_snapshot(), allowed_refs=(REF, OTHER)),
        replace(allowed_snapshot(), denied_refs=(OTHER,)),
        replace(
            allowed_snapshot(), allowed_refs=(), denied_refs=(REF, REF), canonical_chunk_hashes=()
        ),
        replace(allowed_snapshot(), canonical_chunk_hashes=((OTHER, "a" * 64),)),
        replace(allowed_snapshot(), canonical_chunk_hashes=((REF, "a" * 64), (OTHER, "b" * 64))),
        replace(allowed_snapshot(), allowed_refs=(), denied_refs=(REF,)),
        replace(allowed_snapshot(), canonical_chunk_hashes=((REF, "a" * 63),)),
        replace(allowed_snapshot(), canonical_chunk_hashes=((REF, "g" * 64),)),
    ],
)
async def test_extra_missing_or_denied_provenance_is_invalid(snapshot: PolicySnapshot) -> None:
    reader = FakePolicyReader(snapshot)
    decision = await Guard(CharacterCounter()).authorize_read(PRINCIPAL, (REF,), reader)
    assert not decision.allowed
    assert decision.reason == "invalid_provenance"


@pytest.mark.anyio
async def test_stable_distinct_request_and_partial_denial() -> None:
    reader = FakePolicyReader(replace(allowed_snapshot(), denied_refs=(OTHER,)))
    decision = await Guard(CharacterCounter()).authorize_read(
        PRINCIPAL, (OTHER, REF, OTHER, REF), reader
    )
    assert reader.calls == [(PRINCIPAL, (OTHER, REF), None)]
    assert not decision.allowed
    assert decision.reason == "denied"


@pytest.mark.anyio
async def test_admin_without_grant_is_denied() -> None:
    # Administrative status is deliberately absent from the core contract.
    reader = FakePolicyReader(
        replace(
            allowed_snapshot(),
            allowed_refs=(),
            denied_refs=(REF,),
            canonical_chunk_hashes=(),
        )
    )
    decision = await Guard(CharacterCounter()).authorize_read(PRINCIPAL, (REF,), reader)
    assert not decision.allowed
    assert decision.reason == "denied"
