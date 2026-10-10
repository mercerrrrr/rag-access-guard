import stat
import struct
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

import pytest
from tests.helpers.docx_factory import body_xml, paragraph_table_docx, rewrite_docx, with_header

from rag_access_guard_api.adapters import docx_archive, docx_protocol
from rag_access_guard_api.adapters.docx_worker import extract_text
from rag_access_guard_api.services.text_documents import DocumentError


def archive_bytes(
    entries: tuple[tuple[str | ZipInfo, bytes], ...], method: int = ZIP_STORED
) -> bytes:
    buffer = BytesIO()
    with (
        ZipFile(BytesIO(paragraph_table_docx("Safe", (), ""))) as source,
        ZipFile(buffer, "w", method) as archive,
    ):
        for original in source.infolist():
            archive.writestr(original.filename, source.read(original))
        for name, value in entries:
            archive.writestr(name, value)
    return buffer.getvalue()


@pytest.mark.parametrize(
    "names",
    [
        ("A", "a"),
        ("word/%61", "word/a"),
        ("caf\u00e9", "cafe\u0301"),
        ("x/", "x"),
    ],
)
def test_normalized_name_collisions_are_rejected(names: tuple[str, str]) -> None:
    safe = archive_bytes(((names[0], b"a"),))
    assert docx_archive.read_parts(safe)[names[0]] == b"a"
    data = archive_bytes(((names[0], b"a"), (names[1], b"b")))
    with pytest.raises(DocumentError):
        _ = docx_archive.read_parts(data)


@pytest.mark.parametrize("fault", ["symlink", "encrypted", "local-name", "local-flags", "crc"])
def test_hostile_zip_metadata_is_rejected(fault: str) -> None:
    assert docx_archive.read_parts(archive_bytes((("entry", b"payload"),)))["entry"] == b"payload"
    info = ZipInfo("entry")
    if fault == "symlink":
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
    data = bytearray(archive_bytes(((info, b"payload"),)))
    central = data.rindex(b"PK\x01\x02")
    with ZipFile(BytesIO(data)) as archive:
        offset = archive.getinfo("entry").header_offset
    if fault == "encrypted":
        struct.pack_into("<H", data, central + 8, 1)
        struct.pack_into("<H", data, offset + 6, 1)
    elif fault == "local-name":
        data[offset + 30] = ord("X")
    elif fault == "local-flags":
        struct.pack_into("<H", data, offset + 6, 2048)
    elif fault == "crc":
        data[offset + 35] ^= 1
    with pytest.raises(DocumentError):
        _ = docx_archive.read_parts(bytes(data))


@pytest.mark.parametrize("method", [ZIP_STORED, ZIP_DEFLATED])
def test_actual_expansion_cannot_hide_behind_claimed_size(method: int) -> None:
    payload = bytes(range(256)) * 16
    data = bytearray(archive_bytes((("entry", payload),), method))
    assert docx_archive.read_parts(bytes(data))["entry"] == payload
    central = data.rindex(b"PK\x01\x02")
    struct.pack_into("<I", data, central + 24, 1)
    with pytest.raises(DocumentError):
        _ = docx_archive.read_parts(bytes(data))


@pytest.mark.parametrize("fault", ["entry", "total", "ratio", "entries"])
def test_central_directory_resource_limits(fault: str, monkeypatch: pytest.MonkeyPatch) -> None:
    data = archive_bytes((("a", b"x" * 1024), ("b", b"x" * 1024)), ZIP_DEFLATED)
    assert docx_archive.read_parts(data)["a"] == b"x" * 1024
    setting = {
        "entry": "MAX_ENTRY_BYTES",
        "total": "MAX_EXPANDED_BYTES",
        "ratio": "MAX_COMPRESSION_RATIO",
        "entries": "MAX_ENTRIES",
    }[fault]
    monkeypatch.setattr(docx_protocol, setting, 1)

    def unexpected_read(_data: bytes, _entry: ZipInfo, _remaining: int) -> bytes:
        pytest.fail("Central metadata must reject before decompression")

    monkeypatch.setattr(docx_archive, "read_entry", unexpected_read)
    with pytest.raises(DocumentError):
        _ = docx_archive.read_parts(data)


def test_exact_entry_count_is_accepted_before_over_limit_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = paragraph_table_docx("Safe", (), "")
    with ZipFile(BytesIO(raw)) as archive:
        count = len(archive.infolist())
    parts = {f"extra/{index}": b"" for index in range(1000 - count)}
    data = rewrite_docx(raw, parts)
    assert len(docx_archive.read_parts(data)) == 1000

    def unexpected_read(_data: bytes, _entry: ZipInfo, _remaining: int) -> bytes:
        pytest.fail("Entry-count preflight must reject before decompression")

    monkeypatch.setattr(docx_archive, "read_entry", unexpected_read)
    with pytest.raises(DocumentError):
        _ = docx_archive.read_parts(rewrite_docx(data, {"overflow": b""}))


def test_exact_ratio_boundary_applies_to_metadata_and_actual_output() -> None:
    data = archive_bytes((("ratio", b"x" * 1200),), ZIP_DEFLATED)
    with ZipFile(BytesIO(data)) as archive:
        entry = archive.getinfo("ratio")
    assert entry.file_size == entry.compress_size * 100
    assert docx_archive.read_parts(data)["ratio"] == b"x" * 1200
    assert docx_archive.read_entry(data, entry, 67108864) == b"x" * 1200
    over = archive_bytes((("ratio", b"x" * 1201),), ZIP_DEFLATED)
    with ZipFile(BytesIO(over)) as archive:
        entry = archive.getinfo("ratio")
    assert entry.file_size > entry.compress_size * 100
    with pytest.raises(DocumentError):
        _ = docx_archive.read_parts(over)
    with pytest.raises(DocumentError):
        _ = docx_archive.read_entry(over, entry, 67108864)


@pytest.mark.parametrize("method", [ZIP_STORED, ZIP_DEFLATED])
def test_streamed_budget_stops_actual_output(method: int, monkeypatch: pytest.MonkeyPatch) -> None:
    data = archive_bytes((("a", bytes(range(256)) * 4),), method)
    with ZipFile(BytesIO(data)) as archive:
        entry = archive.getinfo("a")
    monkeypatch.setattr(docx_protocol, "MAX_ENTRY_BYTES", 2048)
    assert len(docx_archive.read_entry(data, entry, 1024)) == 1024
    with pytest.raises(DocumentError):
        _ = docx_archive.read_entry(data, entry, 1023)


@pytest.mark.parametrize(
    "payload", [b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"]
)
def test_nested_packages_are_rejected_by_magic(payload: bytes) -> None:
    data = rewrite_docx(paragraph_table_docx("Safe", (), ""), {"word/payload": payload})
    with pytest.raises(DocumentError) as error:
        _ = extract_text(data)
    assert error.value.code == "unsupported_structure"


@pytest.mark.parametrize("fault", ["content-type", "relationship", "part"])
def test_macro_packages_are_explicitly_rejected(fault: str) -> None:
    raw = paragraph_table_docx("Safe", (), "")
    assert extract_text(raw) == "Safe\n\n"
    with ZipFile(BytesIO(raw)) as archive:
        types = archive.read("[Content_Types].xml")
        rels = archive.read("word/_rels/document.xml.rels")
    replacements: dict[str, bytes] = {}
    if fault == "content-type":
        ordinary = (
            b"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
        )
        assert ordinary in types
        replacements["[Content_Types].xml"] = types.replace(
            ordinary, b"application/vnd.ms-word.document.macroEnabled.main+xml"
        )
    elif fault == "relationship":
        replacement = b'<Override PartName="/word/payload" ContentType="application/octet-stream"/>'
        replacements["[Content_Types].xml"] = types.replace(b"</Types>", replacement + b"</Types>")
        replacements["word/payload"] = b"opaque"
        relationship = (
            b'<Relationship Id="custom" Target="payload" '
            b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/customData"/>'
        )
        replacements["word/_rels/document.xml.rels"] = rels.replace(
            b"</Relationships>", relationship + b"</Relationships>"
        )
        assert extract_text(rewrite_docx(raw, replacements)) == "Safe\n\n"
        replacements["word/_rels/document.xml.rels"] = replacements[
            "word/_rels/document.xml.rels"
        ].replace(b"/customData", b"/vbaProject")
    else:
        replacements["word/vbaProject.bin"] = b"opaque"
    with pytest.raises(DocumentError) as error:
        _ = extract_text(rewrite_docx(raw, replacements))
    assert error.value.code == "unsupported_structure"


@pytest.mark.parametrize("story", ["header1", "footer1", "footnotes", "endnotes", "comments"])
def test_nonempty_secondary_stories_are_rejected(story: str) -> None:
    data = rewrite_docx(
        paragraph_table_docx("Safe", (), ""),
        {f"word/{story}.xml": body_xml("<w:p><w:r><w:t>Hidden</w:t></w:r></w:p>")},
    )
    with pytest.raises(DocumentError) as error:
        _ = extract_text(data)
    assert error.value.code == "unsupported_structure"


@pytest.mark.parametrize("kind", ["image", "attachedTemplate", "oleObject", "package"])
def test_external_resource_relationships_are_not_fetched(kind: str) -> None:
    xml = f"""<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
        <Relationship Id="blocked" TargetMode="External" Target="https://example.invalid/private"
        Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/{kind}"/>
        </Relationships>""".encode()
    data = rewrite_docx(paragraph_table_docx("Safe", (), ""), {"word/_rels/extra.xml.rels": xml})
    with pytest.raises(DocumentError) as error:
        _ = extract_text(data)
    assert error.value.code == "unsupported_structure"


def test_multiple_document_bodies_cannot_be_silently_omitted() -> None:
    xml = body_xml("<w:p><w:r><w:t>First</w:t></w:r></w:p>").replace(
        b"</w:document>", b"<w:body><w:p><w:r><w:t>Lost</w:t></w:r></w:p></w:body></w:document>"
    )
    data = rewrite_docx(paragraph_table_docx("", (), ""), {"word/document.xml": xml})
    with pytest.raises(DocumentError) as error:
        _ = extract_text(data)
    assert error.value.code == "unsupported_structure"


def test_property_tail_text_cannot_be_silently_omitted() -> None:
    xml = body_xml("<w:p><w:pPr><w:keepNext/>Lost</w:pPr><w:r><w:t>Safe</w:t></w:r></w:p>")
    data = rewrite_docx(paragraph_table_docx("", (), ""), {"word/document.xml": xml})
    with pytest.raises(DocumentError) as error:
        _ = extract_text(data)
    assert error.value.code == "unsupported_structure"


@pytest.mark.parametrize("part", ["word/header1.xml", "custom/story.xml", "custom/story"])
@pytest.mark.parametrize(
    "content",
    [
        '<w:r><w:sym w:font="Symbol" w:char="F041"/></w:r>',
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r>',
        "<w:sdt><w:sdtContent><w:r/></w:sdtContent></w:sdt>",
        "<w:ins><w:r/></w:ins>",
    ],
)
def test_secondary_story_unsupported_semantics_are_not_silently_omitted(
    content: str, part: str
) -> None:
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    empty = f'<w:hdr xmlns:w="{namespace}"><w:p/></w:hdr>'.encode()
    raw = paragraph_table_docx("Safe", (), "")
    assert extract_text(with_header(raw, part, empty)) == "Safe\n\n"
    hostile = f'<w:hdr xmlns:w="{namespace}"><w:p>{content}</w:p></w:hdr>'.encode()
    with pytest.raises(DocumentError) as error:
        _ = extract_text(with_header(raw, part, hostile))
    assert error.value.code == "unsupported_structure"
