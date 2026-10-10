from zipfile import ZipInfo

import pytest
from tests.helpers.docx_factory import DOCX_MIME, body_xml, paragraph_table_docx, rewrite_docx

from rag_access_guard_api.adapters import docx_archive, docx_protocol
from rag_access_guard_api.adapters.docx_parser import parse_docx
from rag_access_guard_api.adapters.docx_worker import extract_text
from rag_access_guard_api.schemas.ingestion import UploadPayload
from rag_access_guard_api.services.ingestion import prepare_upload
from rag_access_guard_api.services.text_documents import DocumentError


def test_docx_preserves_body_table_order() -> None:
    data = paragraph_table_docx("Начало", (("A", "Б"),), "Конец")
    parsed = prepare_upload(
        UploadPayload(filename="example.docx", media_type=DOCX_MIME, data=data)
    ).parsed
    assert parsed.text == "Начало\n\nA\tБ\n\nКонец"  # noqa: RUF001 -- required Cyrillic fixture
    assert parsed.page_count is None
    assert parsed.empty_page_count is None
    assert parsed.parser_revision == "python-docx-body-v1:1.2.0"


def test_only_document_leading_bom_is_removed() -> None:
    data = paragraph_table_docx("\ufeffFirst", (("\ufeffCell",),), "\ufeffLast")
    assert extract_text(data) == "First\n\n\ufeffCell\n\n\ufeffLast"


@pytest.mark.parametrize(
    "content",
    [
        "<w:p><w:r><w:t>Text</w:t></w:r></w:p><w:sdt><w:sdtContent><w:p><w:r><w:t>Hidden</w:t></w:r></w:p></w:sdtContent></w:sdt>",
        "<w:p><w:ins><w:r><w:t>Changed</w:t></w:r></w:ins></w:p>",
        '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r></w:p>',
        "<w:p><w:r><w:drawing/></w:r></w:p>",
        '<w:tbl><w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr><w:p/></w:tc></w:tr></w:tbl>',
    ],
)
def test_docx_rejects_unsupported_text_without_silent_loss(content: str) -> None:
    data = rewrite_docx(paragraph_table_docx("", (), ""), {"word/document.xml": body_xml(content)})
    with pytest.raises(DocumentError) as error:
        _ = prepare_upload(UploadPayload(filename="example.docx", media_type=DOCX_MIME, data=data))
    assert (error.value.status, error.value.code) == (422, "unsupported_structure")


def test_central_directory_total_rejects_before_decompression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = paragraph_table_docx("Safe", (), "")
    monkeypatch.setattr(docx_protocol, "MAX_EXPANDED_BYTES", 1)

    def unexpected_read(_data: bytes, _entry: ZipInfo, _remaining: int = 0) -> bytes:
        pytest.fail("Declared expanded budget must be checked before any decompression")

    monkeypatch.setattr(docx_archive, "read_entry", unexpected_read)
    with pytest.raises(DocumentError) as error:
        _ = docx_archive.read_parts(data)
    assert error.value.code == "parse_failed"


@pytest.mark.parametrize(
    "content",
    [
        '<w:p><w:r><w:sym w:font="Symbol" w:char="F041"/></w:r></w:p>',
        "<w:p><w:r><w:object/></w:r></w:p>",
        "<w:p><w:r><w:pict/></w:r></w:p>",
        '<w:p><w:fldSimple w:instr="DATE"><w:r><w:t>Ignored</w:t></w:r></w:fldSimple></w:p>',
        '<w:altChunk r:id="rId1"/>',
        '<w:p><w:r><w:footnoteReference w:id="1"/></w:r></w:p>',
        "<w:p><w:del><w:r><w:delText>Deleted</w:delText></w:r></w:del></w:p>",
        "<w:p><w:moveTo><w:r><w:t>Moved</w:t></w:r></w:moveTo></w:p>",
        """<w:p><m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">
        <m:r><m:t>Math</m:t></m:r></m:oMath></w:p>""",
        "<w:p><w:r><w:txbxContent><w:p/></w:txbxContent></w:r></w:p>",
        """<w:tbl><w:tblGrid><w:gridCol/></w:tblGrid><w:tr><w:trPr>
        <w:gridBefore w:val="1"/></w:trPr><w:tc><w:p/></w:tc></w:tr></w:tbl>""",
        "<w:tbl><w:tblGrid><w:gridCol/></w:tblGrid><w:tr><w:tc><w:p/><w:tbl/></w:tc></w:tr></w:tbl>",
    ],
)
def test_unknown_visible_semantics_are_rejected(content: str) -> None:
    data = rewrite_docx(paragraph_table_docx("", (), ""), {"word/document.xml": body_xml(content)})
    with pytest.raises(DocumentError) as error:
        _ = extract_text(data)
    assert error.value.code == "unsupported_structure"


def test_hyperlink_text_tabs_newlines_and_cell_paragraphs_are_preserved() -> None:
    xml = body_xml("""<w:p><w:r><w:t>\ufeffStart</w:t><w:tab/><w:t>Tab</w:t><w:br/></w:r>
        <w:hyperlink r:id="link"><w:r><w:t>Link</w:t></w:r></w:hyperlink></w:p>
        <w:tbl><w:tblGrid><w:gridCol/></w:tblGrid><w:tr><w:tc><w:p><w:r><w:t>A</w:t></w:r></w:p>
        <w:p><w:r><w:t>B</w:t></w:r></w:p></w:tc></w:tr></w:tbl>""")
    rels = b"""<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
    <Relationship Id="link"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
    Target="https://example.invalid/never-fetch" TargetMode="External"/></Relationships>"""
    data = rewrite_docx(
        paragraph_table_docx("", (), ""),
        {"word/document.xml": xml, "word/_rels/document.xml.rels": rels},
    )
    assert extract_text(data) == "Start\tTab\nLink\n\nA\nB"


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-32"])
def test_xml_declarations_are_rejected_semantically(encoding: str) -> None:
    xml = (
        '<?xml version="1.0" encoding="'
        + encoding
        + '"?>'
        + '<!DOCTYPE document [<!ENTITY stolen "PRIVATE">]>'
        + "<document>&stolen;</document>"
    ).encode(encoding)
    with pytest.raises((DocumentError, ValueError)):
        _ = docx_archive.parse_xml(xml)


@pytest.mark.parametrize(
    "name",
    [
        "../outside",
        "/absolute",
        "word\\evil",
        "word/./evil",
        "word//evil",
        "word/%2e%2e/evil",
        "C:evil",
    ],
)
def test_unsafe_entry_names_are_rejected(name: str) -> None:
    data = rewrite_docx(paragraph_table_docx("Safe", (), ""), {name: b"x"})
    with pytest.raises(DocumentError) as error:
        _ = extract_text(data)
    assert error.value.code == "parse_failed"


@pytest.mark.parametrize(
    ("filename", "mime", "data", "status", "code"),
    [
        ("a.doc", DOCX_MIME, b"PK\x03\x04", 415, "unsupported_type"),
        ("a.docx", "application/pdf", b"PK\x03\x04", 415, "unsupported_type"),
        ("../a.docx", DOCX_MIME, b"PK\x03\x04", 422, "parse_failed"),
        ("a.docx", DOCX_MIME, b"not zip", 422, "parse_failed"),
        ("a.docx", DOCX_MIME, b"x" * 10485761, 413, "size_limit"),
    ],
    ids=["old-doc", "wrong-mime", "path", "signature", "upload-limit"],
)
def test_docx_upload_boundary(
    filename: str, mime: str, data: bytes, status: int, code: str
) -> None:
    with pytest.raises(DocumentError) as error:
        _ = parse_docx(UploadPayload(filename=filename, media_type=mime, data=data))
    assert (error.value.status, error.value.code) == (status, code)


def test_empty_docx_reports_empty_text() -> None:
    with pytest.raises(DocumentError) as error:
        _ = parse_docx(
            UploadPayload(
                filename="a.docx", media_type=DOCX_MIME, data=paragraph_table_docx("", (), "")
            )
        )
    assert error.value.code == "empty_text"


def test_corrupt_zip_reports_closed_parse_failure() -> None:
    with pytest.raises(DocumentError) as error:
        _ = parse_docx(
            UploadPayload(filename="a.docx", media_type=DOCX_MIME, data=b"PK\x03\x04broken")
        )
    assert (error.value.status, error.value.code) == (422, "parse_failed")


def test_upload_exactly_ten_mib_is_accepted_but_one_more_byte_is_rejected() -> None:
    base = paragraph_table_docx("Safe", (), "")
    overhead = len(rewrite_docx(base, {"padding": b""}))
    data = rewrite_docx(base, {"padding": b"x" * (10485760 - overhead)})
    assert len(data) == 10485760
    assert (
        parse_docx(UploadPayload(filename="a.docx", media_type=DOCX_MIME, data=data)).text
        == "Safe\n\n"
    )
    with pytest.raises(DocumentError) as error:
        _ = parse_docx(UploadPayload(filename="a.docx", media_type=DOCX_MIME, data=data + b"x"))
    assert (error.value.status, error.value.code) == (413, "size_limit")
