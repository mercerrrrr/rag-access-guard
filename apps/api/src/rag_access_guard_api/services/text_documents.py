"""Bounded UTF-8 text ingestion independent of persistence."""

from typing import Final, Literal

from rag_access_guard_api.schemas.ingestion import IngestionFailure

MAX_TEXT_BYTES: Final = 10_485_760
MAX_UPLOAD_BYTES: Final = MAX_TEXT_BYTES + 16_384
MAX_TITLE_LENGTH: Final = 512


class DocumentError(Exception):
    """A sanitized document boundary failure."""

    def __init__(
        self, status: Literal[404, 413, 415, 422], code: IngestionFailure | None = None
    ) -> None:
        """Retain only the public HTTP status, never submitted data."""
        super().__init__()
        self.status: Literal[404, 413, 415, 422] = status
        self.code: IngestionFailure | None = code


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
