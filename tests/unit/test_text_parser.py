import pytest

from rag_access_guard_api.adapters.text_parser import parse_text
from rag_access_guard_api.schemas.ingestion import UploadPayload
from rag_access_guard_api.services.text_documents import DocumentError


def test_utf8_bom_and_line_endings_are_deterministic() -> None:
    raw = b"\xef\xbb\xbfalpha\r\nbeta\rgamma\n"
    parsed = parse_text(
        UploadPayload(filename="EXAMPLE.TXT", media_type="Text/Plain; charset=utf-8", data=raw)
    )
    assert parsed.text == "alpha\nbeta\ngamma\n"
    assert parsed.parser_revision == "utf8-text-v1"
    assert parsed.media_type == "text/plain"


def test_markdown_remains_unrendered_text() -> None:
    text = "# Заголовок\n<script>literal</script>\ne\u0301"
    for media_type in ("text/plain", "text/markdown"):
        assert (
            parse_text(
                UploadPayload(filename="source.md", media_type=media_type, data=text.encode())
            ).text
            == text
        )


@pytest.mark.parametrize("data", [b"\xff", b"a\x00b", b"\x01binary", b"", b" \t\r\n"])
def test_invalid_utf8_binary_and_empty_fail(data: bytes) -> None:
    with pytest.raises(DocumentError) as failure:
        _ = parse_text(UploadPayload(filename="source.txt", media_type="text/plain", data=data))
    assert failure.value.status == 422


@pytest.mark.parametrize(
    "filename", ["../source.txt", "a/b.txt", "a\\b.txt", "a..txt", "a\x00.txt"]
)
def test_filename_cannot_be_a_path(filename: str) -> None:
    with pytest.raises(DocumentError) as failure:
        _ = parse_text(UploadPayload(filename=filename, media_type="text/plain", data=b"text"))
    assert failure.value.status == 422


@pytest.mark.parametrize(
    ("filename", "media_type"),
    [
        ("x.exe", "text/plain"),
        ("x.txt", "text/markdown"),
        ("x.txt", "application/pdf"),
        ("x.md", "application/octet-stream"),
    ],
)
def test_extension_mime_mismatch_fails(filename: str, media_type: str) -> None:
    with pytest.raises(DocumentError) as failure:
        _ = parse_text(UploadPayload(filename=filename, media_type=media_type, data=b"text"))
    assert failure.value.status == 415


def test_upload_limit_checks_actual_stream_bytes() -> None:
    assert (
        len(
            parse_text(
                UploadPayload(filename="x.txt", media_type="text/plain", data=b"a" * 10_485_760)
            ).text
        )
        == 10_485_760
    )
    with pytest.raises(DocumentError) as failure:
        _ = parse_text(
            UploadPayload(filename="x.txt", media_type="text/plain", data=b"a" * 10_485_761)
        )
    assert failure.value.status == 413
