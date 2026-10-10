"""Validate DOCX uploads and revalidate the bounded worker's closed response."""

from pydantic import ValidationError

from rag_access_guard_api.adapters import docx_process
from rag_access_guard_api.adapters.docx_protocol import (
    DOCX_MIME,
    EMPTY_TEXT,
    INVALID_DOCUMENT,
    MAX_EXTRACTED_BYTES,
    MAX_RESPONSE_BYTES,
    UNSUPPORTED_STRUCTURE,
    DocxResult,
)
from rag_access_guard_api.adapters.text_parser import validate_filename
from rag_access_guard_api.schemas.ingestion import IngestionFailure, ParsedDocument, UploadPayload
from rag_access_guard_api.services.text_documents import MAX_TEXT_BYTES, DocumentError


def parse_docx(upload: UploadPayload) -> ParsedDocument:
    """Accept only validated Word body text, without inventing page metadata."""
    if len(upload.data) > MAX_TEXT_BYTES:
        raise DocumentError(413, "size_limit")
    validate_filename(upload.filename)
    if (
        not upload.filename.lower().endswith(".docx")
        or upload.media_type.split(";", 1)[0].strip().lower() != DOCX_MIME
    ):
        raise DocumentError(415, "unsupported_type")
    if not upload.data.startswith(b"PK\x03\x04"):
        raise DocumentError(422, "parse_failed")
    status, raw = docx_process.run_worker(upload.data)
    failure_codes: dict[int, IngestionFailure] = {
        UNSUPPORTED_STRUCTURE: "unsupported_structure",
        EMPTY_TEXT: "empty_text",
        INVALID_DOCUMENT: "parse_failed",
    }
    if status in failure_codes:
        raise DocumentError(422, failure_codes[status])
    if status != 0:
        raise DocumentError(503, "parse_failed")
    if len(raw) > MAX_RESPONSE_BYTES:
        raise DocumentError(422, "parse_failed")
    try:
        result = DocxResult.model_validate_json(raw)
        size = len(result.text.encode("utf-8"))
    except (UnicodeError, ValidationError):
        raise DocumentError(422, "parse_failed") from None
    if (
        not result.text.strip()
        or "\x00" in result.text
        or "\r" in result.text
        or size > MAX_EXTRACTED_BYTES
    ):
        raise DocumentError(422, "parse_failed")
    return ParsedDocument(
        text=result.text, parser_revision=result.parser_revision, media_type=result.media_type
    )
