"""Bounded UTF-8 text ingestion independent of persistence."""

from dataclasses import dataclass
from hashlib import sha256
from typing import Final, Literal

MAX_TEXT_BYTES: Final = 1_048_576
MAX_UPLOAD_BYTES: Final = MAX_TEXT_BYTES + 16_384
MAX_TITLE_LENGTH: Final = 512


class DocumentError(Exception):
    """A sanitized document boundary failure."""

    def __init__(self, status: Literal[404, 413, 415, 422]) -> None:
        """Retain only the public HTTP status, never submitted data."""
        super().__init__()
        self.status: Literal[404, 413, 415, 422] = status


@dataclass(frozen=True, slots=True)
class ParsedText:
    """Canonical text and hashes of the two distinct representations."""

    text: str
    content_sha256: str
    text_sha256: str


def validate_title(title: str) -> str:
    """Normalize surrounding whitespace and reject invalid database text."""
    normalized = title.strip()
    if not 1 <= len(normalized) <= MAX_TITLE_LENGTH or "\x00" in normalized:
        raise DocumentError(422)
    try:
        _ = normalized.encode("utf-8")
    except UnicodeEncodeError:
        raise DocumentError(422) from None
    return normalized


def parse_text(original_bytes: bytes, filename: str, media_type: str) -> ParsedText:
    """Validate the upload and preserve all text except BOM and newline style."""
    if not filename.lower().endswith(".txt") or media_type.lower() != "text/plain":
        raise DocumentError(415)
    if len(original_bytes) > MAX_TEXT_BYTES:
        raise DocumentError(413)
    try:
        canonical = original_bytes.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError:
        raise DocumentError(422) from None
    if not canonical.strip() or "\x00" in canonical:
        raise DocumentError(422)
    return ParsedText(
        canonical, sha256(original_bytes).hexdigest(), sha256(canonical.encode()).hexdigest()
    )
