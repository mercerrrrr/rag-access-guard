import json
from importlib.metadata import version
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pydantic import JsonValue
from tests.helpers.docx_factory import DOCX_MIME, paragraph_table_docx

from rag_access_guard_api.adapters import docx_parser, docx_process, docx_protocol
from rag_access_guard_api.adapters.docx_worker import extract_text
from rag_access_guard_api.persistence.docx_manifest import DOCX_OPTIONS_JSON
from rag_access_guard_api.schemas.ingestion import UploadPayload
from rag_access_guard_api.services.text_documents import DocumentError


@pytest.mark.parametrize(
    "fault",
    [
        "json",
        "extra",
        "revision",
        "mime",
        "pages",
        "blank",
        "nul",
        "cr",
        "text-limit",
        "response-limit",
    ],
)
def test_parent_revalidates_closed_worker_protocol(
    fault: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload: dict[str, JsonValue] = {
        "text": "Safe",
        "parser_revision": "python-docx-body-v1:1.2.0",
        "media_type": DOCX_MIME,
    }
    changes: dict[str, dict[str, JsonValue]] = {
        "extra": {"private": "Diagnostic"},
        "revision": {"parser_revision": "other"},
        "mime": {"media_type": "text/plain"},
        "pages": {"page_count": 1},
        "blank": {"text": " "},
        "nul": {"text": "x\x00y"},
        "cr": {"text": "x\ry"},
        "text-limit": {"text": "12345"},
    }
    payload.update(changes.get(fault, {}))
    raw = b"{invalid" if fault == "json" else json.dumps(payload).encode()
    if fault == "text-limit":
        monkeypatch.setattr(docx_parser, "MAX_EXTRACTED_BYTES", 4)
    if fault == "response-limit":
        monkeypatch.setattr(docx_parser, "MAX_RESPONSE_BYTES", 4)

    def result(_data: bytes) -> tuple[int, bytes]:
        return 0, raw

    monkeypatch.setattr(docx_process, "run_worker", result)
    with pytest.raises(DocumentError) as error:
        _ = docx_parser.parse_docx(
            UploadPayload(filename="a.docx", media_type=DOCX_MIME, data=b"PK\x03\x04")
        )
    assert (error.value.status, error.value.code) == (422, "parse_failed")


def test_canonical_python_and_sql_recipe_bind_installed_library() -> None:
    assert version("python-docx") == "1.2.0"
    assert json.loads(DOCX_OPTIONS_JSON) == docx_protocol.parser_options()
    assert (
        json.dumps(
            docx_protocol.parser_options(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        == DOCX_OPTIONS_JSON
    )


def test_text_budget_counts_utf8_and_block_separators(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = paragraph_table_docx("A", (), "Б")
    monkeypatch.setattr("rag_access_guard_api.adapters.docx_worker.MAX_EXTRACTED_BYTES", 5)
    assert extract_text(raw) == "A\n\nБ"
    monkeypatch.setattr("rag_access_guard_api.adapters.docx_worker.MAX_EXTRACTED_BYTES", 4)
    with pytest.raises(DocumentError) as error:
        _ = extract_text(raw)
    assert error.value.code == "parse_failed"
