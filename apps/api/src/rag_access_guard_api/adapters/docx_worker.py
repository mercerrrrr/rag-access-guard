"""Parse preflighted DOCX bytes in a bounded, disposable worker."""

import sys
from importlib.metadata import version
from io import BytesIO
from typing import assert_never
from zipfile import ZIP_STORED, ZipFile

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from rag_access_guard_api.adapters.docx_archive import read_parts
from rag_access_guard_api.adapters.docx_protocol import (
    DOCX_MIME,
    DOCX_REVISION,
    MAX_EXTRACTED_BYTES,
    MAX_RESPONSE_BYTES,
    DocxResult,
)
from rag_access_guard_api.adapters.docx_structure import validate_structure
from rag_access_guard_api.services.text_documents import MAX_TEXT_BYTES, DocumentError


def extract_text(data: bytes) -> str:
    """Read document order only after every part and body construct is validated."""
    parts = read_parts(data)
    validate_structure(parts)
    clean = BytesIO()
    with ZipFile(clean, "w", ZIP_STORED) as archive:
        for name, value in parts.items():
            archive.writestr(name, value)
    document = Document(BytesIO(clean.getvalue()))
    blocks: list[str] = []
    size = 0
    for block in document.iter_inner_content():
        match block:
            case Paragraph():
                value = block.text
            case Table():
                value = "\n".join(
                    "\t".join("\n".join(p.text for p in cell.paragraphs) for cell in row.cells)
                    for row in block.rows
                )
            case _:
                assert_never(block)
        value = value.replace("\r\n", "\n").replace("\r", "\n")
        if not blocks:
            value = value.removeprefix("\ufeff")
        size += len(value.encode("utf-8")) + (2 if blocks else 0)
        if size > MAX_EXTRACTED_BYTES or "\x00" in value:
            raise DocumentError(422, "parse_failed")
        blocks.append(value)
    text = "\n\n".join(blocks)
    if not text.strip():
        raise DocumentError(422, "empty_text")
    return text


def main() -> int:
    """Expose only closed failure codes, never library exceptions or diagnostics."""
    try:
        data = sys.stdin.buffer.read(MAX_TEXT_BYTES + 1)
        if version("python-docx") != "1.2.0":
            return 5
        if len(data) > MAX_TEXT_BYTES:
            return 2
        text = extract_text(data)
        response = (
            DocxResult(text=text, parser_revision=DOCX_REVISION, media_type=DOCX_MIME)
            .model_dump_json()
            .encode("utf-8")
        )
        if len(response) > MAX_RESPONSE_BYTES:
            return 2
        _ = sys.stdout.buffer.write(response)
        _ = sys.stdout.buffer.flush()
    except DocumentError as error:
        return {"empty_text": 3, "unsupported_structure": 4}.get(error.code or "", 2)
    except Exception:  # noqa: BLE001 -- disposable untrusted parser boundary
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
