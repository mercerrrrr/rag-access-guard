from hashlib import sha256

import pytest

from rag_access_guard_api.schemas.documents import DocumentSummary
from rag_access_guard_api.services.sources import build_source_url
from tests.helpers.docx_factory import DOCX_MIME, paragraph_table_docx
from tests.helpers.pdf_factory import text_pdf
from tests.support.chat import ChatHttp
from tests.support.downloads import DownloadCase, source_ref


def test_old_original_url_cannot_download_after_revoke(download_case: DownloadCase) -> None:
    case = download_case.case
    before = case.client.get(download_case.original_url)
    assert before.status_code == 200
    assert before.content == download_case.original_bytes
    assert before.headers["content-disposition"].startswith("attachment;")
    case.revoke()
    denied = case.client.get(download_case.original_url)
    assert denied.status_code == 404
    assert denied.json() == {"detail": "Not found"}
    assert "PROTECTED_SYNTHETIC" not in denied.text
    assert denied.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize("kind", ["txt", "md", "pdf", "docx"])
def test_original_returns_exact_stored_bytes_as_attachment(
    download_case: DownloadCase, kind: str
) -> None:
    case = download_case.case
    raw = (
        text_pdf(("SYNTHETIC_PDF",), script=True)
        if kind == "pdf"
        else paragraph_table_docx("DOCX synthetic", (), "")
        if kind == "docx"
        else b"\xef\xbb\xbf# SYNTHETIC\r\n"
    )
    media = {
        "txt": "text/plain",
        "md": "text/markdown",
        "pdf": "application/pdf",
        "docx": DOCX_MIME,
    }[kind]
    uploaded = case.client.post(
        "/api/admin/documents",
        data={"title": "<script>SYNTHETIC</script>"},
        files={"file": (f'quoted"name.{kind}', raw, media)},
        headers=ChatHttp.csrf(case.client),
    )
    assert uploaded.status_code == 201, uploaded.text
    document = DocumentSummary.model_validate_json(uploaded.content)
    ref = source_ref(case, document.id)
    url = build_source_url(ref).replace("/content?", "/original?")
    assert case.client.get(url).status_code == 404
    grant = case.client.post(
        f"/api/admin/documents/{document.id}/grants",
        json={"user_id": str(case.grant.user_id)},
        headers=ChatHttp.csrf(case.client),
    )
    assert grant.status_code == 201
    response = case.client.get(url, headers={"Range": "bytes=0-2", "If-None-Match": "*"})
    assert response.status_code == 200
    assert response.content == raw
    assert sha256(response.content).digest() == sha256(raw).digest()
    assert response.headers["content-type"] == media + (
        "; charset=utf-8" if kind in {"txt", "md"} else ""
    )
    assert response.headers["content-disposition"] == f'attachment; filename="{document.id}.{kind}"'
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["vary"] == "Cookie"
    assert "content-range" not in response.headers
    assert "location" not in response.headers
