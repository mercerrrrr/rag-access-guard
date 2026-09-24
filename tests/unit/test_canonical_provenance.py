from hashlib import sha256
from uuid import uuid4

import pytest

from rag_access_guard_api.adapters.canonical import CanonicalChunk
from rag_access_guard_api.adapters.tokenizer import get_tokenizer
from rag_access_guard_api.schemas.search import InvalidProvenanceError


def test_allowed_uuid_cannot_launder_other_document_text() -> None:
    forged = "CLOSED_OTHER_SOURCE"
    row = CanonicalChunk(
        document_id=uuid4(),
        document_version_id=uuid4(),
        chunk_id=uuid4(),
        text=forged,
        content_sha256=sha256(forged.encode()).hexdigest(),
        token_count=get_tokenizer().count(forged),
        char_start=0,
        char_end=len(forged),
        extracted_text="ALLOWED_CANONICAL_SOURCE",
    )
    with pytest.raises(InvalidProvenanceError):
        _ = row.candidate()


def test_token_count_cannot_be_forged() -> None:
    content = "test"
    row = CanonicalChunk(
        document_id=uuid4(),
        document_version_id=uuid4(),
        chunk_id=uuid4(),
        text=content,
        content_sha256=sha256(content.encode()).hexdigest(),
        token_count=99,
        char_start=0,
        char_end=len(content),
        extracted_text=content,
    )
    with pytest.raises(InvalidProvenanceError):
        _ = row.candidate()
