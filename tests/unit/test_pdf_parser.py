import pytest
from tests.helpers.pdf_factory import text_pdf

from rag_access_guard_api.adapters.pdf_parser import parse_pdf
from rag_access_guard_api.schemas.ingestion import UploadPayload
from rag_access_guard_api.services.ingestion import prepare_upload
from rag_access_guard_api.services.text_documents import DocumentError


def test_pdf_text_pages_join_deterministically() -> None:
    prepared = prepare_upload(
        UploadPayload(
            filename="source.pdf",
            media_type="application/pdf",
            data=text_pdf(("First", "Second")),
        )
    )
    assert prepared.parsed.text == "First\n\nSecond"
    assert prepared.parsed.parser_revision == "pypdf-plain-v1:6.19.0"


@pytest.mark.parametrize(
    "data",
    [text_pdf(("secret",), encrypted=True), b"%PDF-broken", text_pdf(("x",) * 201)],
    ids=["encrypted", "malformed", "page-limit"],
)
def test_encrypted_malformed_and_over_200_pages_are_rejected(data: bytes) -> None:
    with pytest.raises(DocumentError) as error:
        _ = prepare_upload(
            UploadPayload(filename="bad.pdf", media_type="application/pdf", data=data)
        )
    assert error.value.status == 422


def test_pdf_javascript_and_attachments_are_not_extracted() -> None:
    prepared = prepare_upload(
        UploadPayload(
            filename="source.pdf",
            media_type="application/pdf",
            data=text_pdf(("Safe text",), script=True),
        )
    )
    assert prepared.parsed.text == "Safe text"


def test_pdf_mixed_pages_keep_exact_separators_and_counters() -> None:
    parsed = parse_pdf(
        UploadPayload(
            filename="mixed.pdf",
            media_type="application/pdf",
            data=text_pdf(("Русский текст", "", "English")),
        )
    )
    assert parsed.text == "Русский текст\n\n\n\nEnglish"
    assert parsed.page_count == 3
    assert parsed.empty_page_count == 1


def test_pdf_page_limit_is_inclusive() -> None:
    parsed = parse_pdf(
        UploadPayload(
            filename="limit.pdf", media_type="application/pdf", data=text_pdf(("x",) * 200)
        )
    )
    assert parsed.page_count == 200


def test_worker_rejects_extracted_text_over_limit() -> None:
    raw = text_pdf(("x" * (8 * 1024 * 1024 + 1),), compressed=True)
    assert len(raw) < 10 * 1024 * 1024
    with pytest.raises(DocumentError) as error:
        _ = parse_pdf(UploadPayload(filename="large.pdf", media_type="application/pdf", data=raw))
    assert error.value.status == 422


@pytest.mark.parametrize(
    ("filename", "mime", "data", "status"),
    [
        ("wrong.txt", "application/pdf", b"%PDF-", 415),
        ("wrong.pdf", "text/plain", b"%PDF-", 415),
        ("../wrong.pdf", "application/pdf", b"%PDF-", 422),
        ("fake.pdf", "application/pdf", b"not pdf", 422),
        ("huge.pdf", "application/pdf", b"x" * (10 * 1024 * 1024 + 1), 413),
    ],
    ids=["extension", "mime", "path", "signature", "upload-limit"],
)
def test_pdf_upload_boundaries(filename: str, mime: str, data: bytes, status: int) -> None:
    with pytest.raises(DocumentError) as error:
        _ = parse_pdf(UploadPayload(filename=filename, media_type=mime, data=data))
    assert error.value.status == status
