from dataclasses import dataclass, replace
from hashlib import sha256
from uuid import UUID

import pytest

from rag_access_guard import CandidateChunk, Guard, PolicySnapshot, PreparedContext, SourceRef
from rag_access_guard.fingerprint import fingerprint


@dataclass(frozen=True, slots=True)
class Counter:
    identity: str = "characters-v1"

    def count(self, text: str) -> int:
        return len(text)


@dataclass(frozen=True, slots=True)
class Reader:
    value: PolicySnapshot

    async def snapshot(
        self,
        principal_id: UUID,
        source_refs: tuple[SourceRef, ...],
        *,
        thread_id: UUID | None = None,
    ) -> PolicySnapshot:
        _ = principal_id, source_refs, thread_id
        return self.value


def candidate() -> CandidateChunk:
    return CandidateChunk(
        source_ref=SourceRef(
            document_id=UUID(int=2), document_version_id=UUID(int=3), chunk_id=UUID(int=4)
        ),
        text="PUBLIC_MARKER",
        content_sha256=sha256(b"PUBLIC_MARKER").hexdigest(),
        token_count=1,
    )


def reader() -> Reader:
    chunk = candidate()
    return Reader(
        PolicySnapshot(
            principal_id=UUID(int=1),
            revision=7,
            allowed_refs=(chunk.source_ref,),
            denied_refs=(),
            provenance_valid=True,
            principal_active=True,
            thread_owned=True,
            canonical_chunk_hashes=((chunk.source_ref, chunk.content_sha256),),
        )
    )


@pytest.mark.anyio
async def test_prepare_and_release_canonical_context() -> None:
    guard = Guard(Counter())
    prepared = await guard.prepare_context(UUID(int=1), (candidate(),), (), reader())
    assert isinstance(prepared, PreparedContext)
    assert "PUBLIC_MARKER" in prepared.model_context
    release = await guard.authorize_release(UUID(int=1), UUID(int=5), prepared, reader())
    assert release.allowed


@pytest.mark.anyio
async def test_self_consistent_substituted_text_is_denied() -> None:
    fake = replace(
        candidate(), text="PRIVATE_MARKER", content_sha256=sha256(b"PRIVATE_MARKER").hexdigest()
    )
    prepared = await Guard(Counter()).prepare_context(UUID(int=1), (fake,), (), reader())
    assert not isinstance(prepared, PreparedContext)
    assert prepared.reason == "invalid_provenance"


@pytest.mark.anyio
async def test_duplicate_json_field_cannot_hide_additional_model_context() -> None:
    guard = Guard(Counter())
    prepared = await guard.prepare_context(UUID(int=1), (candidate(),), (), reader())
    assert isinstance(prepared, PreparedContext)
    forged = replace(
        prepared,
        model_context=prepared.model_context.replace(
            '{"chunks":',
            '{"chunks":["PRIVATE_MARKER"],"chunks":',
            1,
        ),
    )
    forged = replace(forged, fingerprint=fingerprint(forged, Counter().identity, 5000))
    release = await guard.authorize_release(UUID(int=1), UUID(int=5), forged, reader())
    assert not release.allowed
    assert release.reason == "invalid_provenance"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "change", ["revision", "owner", "principal", "fingerprint", "text", "refs"]
)
async def test_release_rejects_changed_binding(change: str) -> None:
    guard = Guard(Counter())
    prepared = await guard.prepare_context(UUID(int=1), (candidate(),), (), reader())
    assert isinstance(prepared, PreparedContext)
    policy = reader()
    if change == "revision":
        policy = Reader(replace(policy.value, revision=8))
    elif change == "owner":
        policy = Reader(replace(policy.value, thread_owned=False))
    elif change == "principal":
        policy = Reader(replace(policy.value, principal_id=UUID(int=8)))
    elif change == "fingerprint":
        prepared = replace(prepared, fingerprint="a" * 64)
    elif change == "text":
        prepared = replace(
            prepared,
            model_context=prepared.model_context.replace("PUBLIC_MARKER", "PRIVATE_MARKER"),
        )
        prepared = replace(prepared, fingerprint=fingerprint(prepared, Counter().identity, 5000))
    else:
        prepared = replace(prepared, source_refs=())
    release = await guard.authorize_release(UUID(int=1), UUID(int=5), prepared, policy)
    assert not release.allowed


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["denied", "unknown", "principal", "budget"])
async def test_prepare_excludes_denied_or_unverifiable_context(mode: str) -> None:
    policy = reader()
    guard = Guard(Counter(), max_context_tokens=1 if mode == "budget" else 5000)
    if mode == "denied":
        policy = Reader(
            replace(
                policy.value,
                allowed_refs=(),
                denied_refs=(candidate().source_ref,),
                canonical_chunk_hashes=(),
            )
        )
    elif mode == "unknown":
        policy = Reader(replace(policy.value, canonical_chunk_hashes=()))
    elif mode == "principal":
        policy = Reader(replace(policy.value, principal_active=False))
    prepared = await guard.prepare_context(UUID(int=1), (candidate(),), (), policy)
    if isinstance(prepared, PreparedContext):
        assert mode in {"budget", "denied"}
        assert prepared.source_refs == ()
        assert prepared.model_context == ""
    else:
        assert mode in {"unknown", "principal"}


@pytest.mark.anyio
async def test_fingerprint_binds_tokenizer_identity() -> None:
    prepared = await Guard(Counter()).prepare_context(UUID(int=1), (candidate(),), (), reader())
    assert isinstance(prepared, PreparedContext)
    result = await Guard(Counter(identity="changed")).authorize_release(
        UUID(int=1), UUID(int=5), prepared, reader()
    )
    assert not result.allowed
