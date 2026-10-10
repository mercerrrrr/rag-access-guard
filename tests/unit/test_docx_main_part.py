from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from tests.helpers.docx_factory import DOCX_MIME, body_xml, paragraph_table_docx

from rag_access_guard_api.adapters.docx_archive import parse_xml
from rag_access_guard_api.adapters.docx_parser import parse_docx
from rag_access_guard_api.adapters.docx_worker import extract_text
from rag_access_guard_api.schemas.ingestion import UploadPayload
from rag_access_guard_api.services.text_documents import DocumentError


def _alternate_parts(content: bytes, *, decoy: bool = True) -> dict[str, bytes]:
    with ZipFile(BytesIO(paragraph_table_docx("DECOY", (), ""))) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    parts["_rels/.rels"] = parts["_rels/.rels"].replace(
        b'Target="word/document.xml"', b'Target="custom/document.xml"'
    )
    rels = parse_xml(parts["word/_rels/document.xml.rels"])
    for rel in rels:
        rel.set("Target", "../word/" + rel.get("Target", ""))
    parts["custom/_rels/document.xml.rels"] = ET.tostring(rels)
    types = parse_xml(parts["[Content_Types].xml"])
    main = next(item for item in types if item.get("PartName") == "/word/document.xml")
    override = ET.SubElement(types, main.tag, dict(main.attrib))
    override.set("PartName", "/custom/document.xml")
    if not decoy:
        del parts["word/document.xml"]
        del parts["word/_rels/document.xml.rels"]
        types.remove(main)
    parts["[Content_Types].xml"] = ET.tostring(types)
    parts["custom/document.xml"] = content
    return parts


def _package(parts: dict[str, bytes]) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    return output.getvalue()


@pytest.mark.parametrize("decoy", [True, False])
def test_alternate_main_preserves_actual_paragraph_table_order(*, decoy: bool) -> None:
    with ZipFile(BytesIO(paragraph_table_docx("ACTUAL", (("A", "B"),), "END"))) as archive:
        content = archive.read("word/document.xml")
    data = _package(_alternate_parts(content, decoy=decoy))
    assert extract_text(data) == "ACTUAL\n\nA\tB\n\nEND"


@pytest.mark.parametrize(
    "unsupported",
    [
        """<w:sdt><w:sdtContent><w:p><w:r><w:t>LOST_UNSUPPORTED_TEXT</w:t></w:r>
        </w:p></w:sdtContent></w:sdt>""",
        "<w:p><w:ins><w:r><w:t>LOST_TRACKED_TEXT</w:t></w:r></w:ins></w:p>",
        "<w:p><w:r><w:drawing/></w:r></w:p>",
    ],
    ids=["content-control", "tracked", "drawing"],
)
def test_unsupported_actual_main_is_rejected_despite_safe_decoy(unsupported: str) -> None:
    safe = "<w:p><w:r><w:t>ACTUAL_MAIN_SAFE</w:t></w:r></w:p>"
    assert extract_text(_package(_alternate_parts(body_xml(safe)))) == "ACTUAL_MAIN_SAFE"
    data = _package(_alternate_parts(body_xml(safe + unsupported)))
    with pytest.raises(DocumentError) as error:
        _ = parse_docx(UploadPayload(filename="actual.docx", media_type=DOCX_MIME, data=data))
    assert (error.value.status, error.value.code) == (422, "unsupported_structure")


@pytest.mark.parametrize(
    "mutation",
    [
        "ambiguous",
        "missing-office",
        "duplicate-id",
        "missing-id",
        "missing-target",
        "alias",
        "percent-alias",
        "foreign-type",
    ],
)
def test_main_selection_is_closed_when_ambiguous_or_mismatched(mutation: str) -> None:
    parts = _alternate_parts(body_xml("<w:p><w:r><w:t>ACTUAL</w:t></w:r></w:p>"))
    assert extract_text(_package(parts)) == "ACTUAL"
    relationships = parse_xml(parts["_rels/.rels"])
    main = next(rel for rel in relationships if rel.get("Type", "").endswith("/officeDocument"))
    match mutation:
        case "ambiguous":
            duplicate = ET.SubElement(relationships, main.tag, dict(main.attrib))
            duplicate.set("Id", "ambiguous")
            duplicate.set("Target", "word/document.xml")
        case "missing-office":
            relationships.remove(main)
        case "duplicate-id":
            next(rel for rel in relationships if rel is not main).set("Id", main.get("Id", ""))
        case "missing-id":
            _ = main.attrib.pop("Id")
        case "missing-target":
            del parts["custom/document.xml"]
        case "alias":
            main.set("Target", "custom/../custom/document.xml")
        case "percent-alias":
            main.set("Target", "custom/%64ocument.xml")
        case "foreign-type":
            main.set("Type", "https://example.invalid/officeDocument")
        case _:
            pytest.fail("Unknown main-part mutation")
    parts["_rels/.rels"] = ET.tostring(relationships)
    with pytest.raises(DocumentError) as error:
        _ = extract_text(_package(parts))
    assert (error.value.status, error.value.code) == (422, "parse_failed")


@pytest.mark.parametrize("duplicate", [False, True])
def test_main_content_type_is_closed_when_wrong_or_ambiguous(*, duplicate: bool) -> None:
    parts = _alternate_parts(body_xml("<w:p><w:r><w:t>ACTUAL</w:t></w:r></w:p>"))
    assert extract_text(_package(parts)) == "ACTUAL"
    types = parse_xml(parts["[Content_Types].xml"])
    override = next(item for item in types if item.get("PartName") == "/custom/document.xml")
    if duplicate:
        _ = ET.SubElement(types, override.tag, dict(override.attrib))
    else:
        override.set("ContentType", "application/xml")
    parts["[Content_Types].xml"] = ET.tostring(types)
    with pytest.raises(DocumentError) as error:
        _ = extract_text(_package(parts))
    assert (error.value.status, error.value.code) == (422, "parse_failed")
