"""Versioned PDF extraction limits and the worker's closed response schema."""

from importlib.metadata import version
from typing import ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

PDF_REVISION: Final = f"pypdf-plain-v1:{version('pypdf')}"
MAX_PAGES: Final = 200
MAX_EXTRACTED_BYTES: Final = 8 * 1024 * 1024
MAX_RESPONSE_BYTES: Final = 16 * 1024 * 1024 + 65536
MAX_RSS_BYTES: Final = 512 * 1024 * 1024
TIMEOUT_SECONDS: Final = 30
POLL_SECONDS: Final = 0.05
INVALID_DOCUMENT: Final = 2
NO_TEXT_LAYER: Final = 3


class PdfResult(BaseModel):
    """Only canonical text and non-sensitive extraction counters cross the pipe."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid", strict=True)
    text: str
    parser_revision: str
    media_type: Literal["application/pdf"]
    page_count: int = Field(ge=1, le=MAX_PAGES)
    empty_page_count: int = Field(ge=0, lt=MAX_PAGES)


def parser_options() -> dict[str, str | int]:
    """Return the immutable recipe included in PDF configuration hashes."""
    return {
        "extraction_mode": "plain",
        "max_input_bytes": 10485760,
        "max_pages": MAX_PAGES,
        "max_response_bytes": MAX_RESPONSE_BYTES,
        "max_rss_bytes": MAX_RSS_BYTES,
        "max_text_bytes": MAX_EXTRACTED_BYTES,
        "newline": "LF",
        "page_separator": "\n\n",
        "poll_ms": 50,
        "strict": 1,
        "timeout_seconds": TIMEOUT_SECONDS,
    }
