"""Closed DOCX worker messages and the versioned extraction recipe."""

from typing import ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict

DOCX_MIME: Final = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
DOCX_REVISION: Final = "python-docx-body-v1:1.2.0"
MAX_ENTRIES: Final = 1000
MAX_EXPANDED_BYTES: Final = 64 * 1024 * 1024
MAX_ENTRY_BYTES: Final = 16 * 1024 * 1024
MAX_COMPRESSION_RATIO: Final = 100
MAX_EXTRACTED_BYTES: Final = 8 * 1024 * 1024
MAX_RESPONSE_BYTES: Final = 16 * 1024 * 1024 + 65536
MAX_RSS_BYTES: Final = 512 * 1024 * 1024
TIMEOUT_SECONDS: Final = 30
POLL_SECONDS: Final = 0.05
INVALID_DOCUMENT: Final = 2
EMPTY_TEXT: Final = 3
UNSUPPORTED_STRUCTURE: Final = 4


class DocxResult(BaseModel):
    """No optional or diagnostic fields cross the worker boundary."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid", strict=True)
    text: str
    parser_revision: Literal["python-docx-body-v1:1.2.0"]
    media_type: Literal["application/vnd.openxmlformats-officedocument.wordprocessingml.document"]
    page_count: None = None
    empty_page_count: None = None


def parser_options() -> dict[str, str | int]:
    """Bind every limit and canonical separator to the immutable config hash."""
    return {
        "block_separator": "\n\n",
        "cell_paragraph_separator": "\n",
        "cell_separator": "\t",
        "library_revision": "1.2.0",
        "max_compression_ratio": MAX_COMPRESSION_RATIO,
        "max_entries": MAX_ENTRIES,
        "max_entry_bytes": MAX_ENTRY_BYTES,
        "max_expanded_bytes": MAX_EXPANDED_BYTES,
        "max_input_bytes": 10485760,
        "max_response_bytes": MAX_RESPONSE_BYTES,
        "max_rss_bytes": MAX_RSS_BYTES,
        "max_text_bytes": MAX_EXTRACTED_BYTES,
        "newline": "LF",
        "poll_ms": 50,
        "row_separator": "\n",
        "timeout_seconds": TIMEOUT_SECONDS,
        "unsupported_policy": "closed-body-paragraph-table-v1",
    }
