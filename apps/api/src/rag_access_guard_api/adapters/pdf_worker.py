"""Read untrusted PDF bytes in a disposable process; emit no parser diagnostics."""

import logging
import sys
from io import BytesIO

from pypdf import PdfReader

from rag_access_guard_api.adapters.pdf_protocol import (
    MAX_EXTRACTED_BYTES,
    MAX_PAGES,
    MAX_RESPONSE_BYTES,
    PDF_REVISION,
    PdfResult,
)
from rag_access_guard_api.services.text_documents import MAX_TEXT_BYTES


def main() -> int:  # noqa: PLR0911
    """Exit 2 for invalid input, 3 for absent text; never execute PDF actions."""
    logging.disable(logging.CRITICAL)
    try:
        data = sys.stdin.buffer.read(MAX_TEXT_BYTES + 1)
        if len(data) > MAX_TEXT_BYTES or not data.startswith(b"%PDF-"):
            return 2
        reader = PdfReader(BytesIO(data), strict=True)
        if reader.is_encrypted or not 1 <= len(reader.pages) <= MAX_PAGES:
            return 2
        pages: list[str] = []
        size = 0
        for page in reader.pages:
            value = (
                (page.extract_text(extraction_mode="plain") or "")
                .replace("\r\n", "\n")
                .replace("\r", "\n")
            )
            size += len(value.encode("utf-8")) + (2 if pages else 0)
            if size > MAX_EXTRACTED_BYTES or "\x00" in value:
                return 2
            pages.append(value)
        if not any(pages):
            return 3
        text = "\n\n".join(pages)
        if not text.strip():
            return 2
        result = (
            PdfResult(
                text=text,
                parser_revision=PDF_REVISION,
                media_type="application/pdf",
                page_count=len(pages),
                empty_page_count=sum(not page.strip() for page in pages),
            )
            .model_dump_json()
            .encode("utf-8")
        )
        if len(result) > MAX_RESPONSE_BYTES:
            return 2
        _ = sys.stdout.buffer.write(result)
        _ = sys.stdout.buffer.flush()
    except Exception:  # noqa: BLE001
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
