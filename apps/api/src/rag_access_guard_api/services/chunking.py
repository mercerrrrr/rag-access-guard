"""Exact canonical-text windows with E5 content-token bounds."""

from dataclasses import dataclass
from hashlib import sha256
from itertools import pairwise
from typing import Final

from rag_access_guard_api.adapters.tokenizer import E5TokenCounter
from rag_access_guard_api.services.text_documents import DocumentError

CHUNKER_REVISION: Final = "e5-window400-overlap50-offsets-v1"
CONTENT_LIMIT: Final = 400
OVERLAP: Final = 50
EMBEDDING_LIMIT: Final = 512


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    """A canonical substring awaiting its immutable version identity."""

    ordinal: int
    text: str
    content_sha256: str
    token_count: int
    char_start: int
    char_end: int


def chunk_text(text: str, tokenizer: E5TokenCounter) -> tuple[ChunkDraft, ...]:
    """Split canonical text into bounded, overlapping exact substrings."""
    if not text.strip():
        raise DocumentError(422, "empty_text")
    offsets = tokenizer.offsets(text)
    if (
        not offsets
        or any(start < 0 or end <= start or end > len(text) for start, end in offsets)
        or any(left[0] > right[0] or left[1] > right[1] for left, right in pairwise(offsets))
    ):
        raise DocumentError(422, "parse_failed")
    chunks: list[ChunkDraft] = []
    start_token = 0
    covered = 0
    while start_token < len(offsets):
        end_token = min(start_token + CONTENT_LIMIT, len(offsets))
        char_start = offsets[start_token][0] if chunks else 0
        while end_token > start_token:
            char_end = len(text) if end_token == len(offsets) else offsets[end_token - 1][1]
            body = text[char_start:char_end]
            count = tokenizer.count(body)
            if 0 < count <= CONTENT_LIMIT:
                break
            end_token -= 1
        else:
            raise DocumentError(422, "parse_failed")
        if (
            char_start > covered
            or char_end <= covered
            or (tokenizer.embedding_input_tokens(body, kind="passage") > EMBEDDING_LIMIT)
        ):
            raise DocumentError(422, "parse_failed")
        chunks.append(
            ChunkDraft(
                len(chunks),
                body,
                sha256(body.encode("utf-8")).hexdigest(),
                count,
                char_start,
                char_end,
            )
        )
        covered = char_end
        if end_token == len(offsets):
            break
        start_token = max(start_token + 1, end_token - OVERLAP)
    return tuple(chunks)
