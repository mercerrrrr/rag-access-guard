"""Deterministic UTF-8 extraction without interpreting document instructions."""

import re

from rag_access_guard_api.schemas.ingestion import ParsedDocument, UploadPayload
from rag_access_guard_api.services.text_documents import MAX_TEXT_BYTES, DocumentError


def validate_filename(filename: str) -> None:
    """Reject path syntax before any transport-specific basename normalization."""
    if any(part in filename for part in ("..", "/", "\\", "\x00")):
        raise DocumentError(422, "parse_failed")


def parse_text(upload: UploadPayload) -> ParsedDocument:
    """Preserve Unicode and Markdown, normalizing only BOM and newlines."""
    if len(upload.data) > MAX_TEXT_BYTES:
        raise DocumentError(413, "size_limit")
    validate_filename(upload.filename)
    extension = upload.filename.lower().rsplit(".", 1)[-1]
    media_type = upload.media_type.split(";", 1)[0].strip().lower()
    allowed = {"txt": {"text/plain"}, "md": {"text/plain", "text/markdown"}}
    if media_type not in allowed.get(extension, set()) or "." not in upload.filename:
        raise DocumentError(415, "unsupported_type")
    try:
        canonical = upload.data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError:
        raise DocumentError(422, "invalid_encoding") from None
    if re.search(r"[\x00-\x08\x0b-\x1f\x7f]", canonical):
        raise DocumentError(422, "invalid_encoding")
    if not canonical.strip():
        raise DocumentError(422, "empty_text")
    return ParsedDocument(text=canonical, parser_revision="utf8-text-v1", media_type=media_type)
