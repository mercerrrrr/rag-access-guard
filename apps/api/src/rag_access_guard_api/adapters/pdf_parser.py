"""Validate PDF uploads and the isolated parser's untrusted response."""

from pydantic import ValidationError

from rag_access_guard_api.adapters import pdf_process
from rag_access_guard_api.adapters.pdf_protocol import (
    INVALID_DOCUMENT,
    MAX_EXTRACTED_BYTES,
    MAX_RESPONSE_BYTES,
    NO_TEXT_LAYER,
    PDF_REVISION,
    PdfResult,
)
from rag_access_guard_api.adapters.text_parser import validate_filename
from rag_access_guard_api.schemas.ingestion import ParsedDocument, UploadPayload
from rag_access_guard_api.services.text_documents import MAX_TEXT_BYTES, DocumentError


def parse_pdf(upload: UploadPayload) -> ParsedDocument:
    """Extract a text layer without OCR, file attachments or active PDF content."""
    if len(upload.data) > MAX_TEXT_BYTES:
        raise DocumentError(413, "size_limit")
    validate_filename(upload.filename)
    if (
        not upload.filename.lower().endswith(".pdf")
        or upload.media_type.split(";", 1)[0].strip().lower() != "application/pdf"
    ):
        raise DocumentError(415, "unsupported_type")
    if not upload.data.startswith(b"%PDF-"):
        raise DocumentError(422, "parse_failed")
    status, raw = pdf_process.run_worker(upload.data)
    if status == NO_TEXT_LAYER:
        raise DocumentError(422, "text_layer_required")
    if status == INVALID_DOCUMENT:
        raise DocumentError(422, "parse_failed")
    if status != 0:
        raise DocumentError(503, "parse_failed")
    if len(raw) > MAX_RESPONSE_BYTES:
        raise DocumentError(422, "parse_failed")
    try:
        result = PdfResult.model_validate_json(raw)
        text_size = len(result.text.encode("utf-8"))
    except (UnicodeError, ValidationError):
        raise DocumentError(422, "parse_failed") from None
    _validate_result(result, text_size)
    return ParsedDocument(
        text=result.text,
        parser_revision=result.parser_revision,
        media_type=result.media_type,
        page_count=result.page_count,
        empty_page_count=result.empty_page_count,
    )


def _validate_result(result: PdfResult, text_size: int) -> None:
    if result.parser_revision != PDF_REVISION or result.empty_page_count >= result.page_count:
        raise DocumentError(422, "parse_failed")
    if (
        not result.text.strip()
        or "\x00" in result.text
        or "\r" in result.text
        or text_size > MAX_EXTRACTED_BYTES
    ):
        raise DocumentError(422, "parse_failed")
