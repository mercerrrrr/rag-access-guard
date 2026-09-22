from dataclasses import FrozenInstanceError
from uuid import UUID

import pytest

from rag_access_guard import CandidateChunk, PolicySnapshot, SourceRef


def source_ref() -> SourceRef:
    return SourceRef(document_id=UUID(int=1), document_version_id=UUID(int=2), chunk_id=UUID(int=3))


@pytest.mark.parametrize("field", ["document_id", "document_version_id", "chunk_id"])
def test_frozen_types_reject_mutation(field: str) -> None:
    ref = source_ref()
    with pytest.raises(FrozenInstanceError):
        setattr(ref, field, UUID(int=9))


@pytest.mark.parametrize("digest", ["", "a" * 63, "A" * 64, "g" * 64])
def test_candidate_rejects_invalid_hash(digest: str) -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        _ = CandidateChunk(
            source_ref=source_ref(), text="text", content_sha256=digest, token_count=1
        )


def test_candidate_rejects_negative_token_count() -> None:
    with pytest.raises(ValueError, match="nonnegative"):
        _ = CandidateChunk(
            source_ref=source_ref(), text="text", content_sha256="a" * 64, token_count=-1
        )


def test_snapshot_rejects_negative_revision() -> None:
    with pytest.raises(ValueError, match="nonnegative"):
        _ = PolicySnapshot(
            principal_id=UUID(int=1),
            revision=-1,
            allowed_refs=(),
            denied_refs=(),
            provenance_valid=True,
            principal_active=True,
            thread_owned=None,
            canonical_chunk_hashes=(),
        )
