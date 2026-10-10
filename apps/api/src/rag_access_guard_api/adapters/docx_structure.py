"""Closed Word body structure prevents silent loss through library convenience APIs."""

from typing import Final
from xml.etree.ElementTree import Element

from rag_access_guard_api.adapters.docx_archive import main_document_name, parse_xml, xml_part_names
from rag_access_guard_api.services.text_documents import DocumentError

W: Final = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_CHILDREN: Final = {
    "document": {"body"},
    "body": {"p", "tbl", "sectPr"},
    "p": {"pPr", "r", "hyperlink", "bookmarkStart", "bookmarkEnd", "proofErr"},
    "hyperlink": {"r", "bookmarkStart", "bookmarkEnd", "proofErr"},
    "r": {"rPr", "t", "tab", "br", "cr", "lastRenderedPageBreak"},
    "tbl": {"tblPr", "tblGrid", "tr"},
    "tr": {"trPr", "tc"},
    "tc": {"tcPr", "p"},
    "tblGrid": {"gridCol"},
}
_PROPERTIES: Final = {
    "pPr",
    "rPr",
    "tblPr",
    "trPr",
    "tcPr",
    "sectPr",
    "pStyle",
    "keepNext",
    "keepLines",
    "pageBreakBefore",
    "widowControl",
    "numPr",
    "ilvl",
    "numId",
    "pBdr",
    "top",
    "left",
    "bottom",
    "right",
    "between",
    "bar",
    "shd",
    "tabs",
    "tab",
    "suppressAutoHyphens",
    "spacing",
    "ind",
    "contextualSpacing",
    "jc",
    "textDirection",
    "textAlignment",
    "outlineLvl",
    "divId",
    "cnfStyle",
    "rFonts",
    "b",
    "bCs",
    "i",
    "iCs",
    "caps",
    "smallCaps",
    "strike",
    "dstrike",
    "outline",
    "shadow",
    "emboss",
    "imprint",
    "noProof",
    "snapToGrid",
    "vanish",
    "webHidden",
    "color",
    "sz",
    "szCs",
    "highlight",
    "u",
    "effect",
    "bdr",
    "fitText",
    "vertAlign",
    "rtl",
    "cs",
    "em",
    "lang",
    "eastAsianLayout",
    "rStyle",
    "tblStyle",
    "tblpPr",
    "tblOverlap",
    "bidiVisual",
    "tblStyleRowBandSize",
    "tblStyleColBandSize",
    "tblW",
    "tblCellSpacing",
    "tblInd",
    "tblBorders",
    "insideH",
    "insideV",
    "tblLayout",
    "tblCellMar",
    "tblLook",
    "cantSplit",
    "trHeight",
    "tblHeader",
    "tcW",
    "tcBorders",
    "noWrap",
    "tcMar",
    "vAlign",
    "hideMark",
    "headerReference",
    "footerReference",
    "type",
    "pgSz",
    "pgMar",
    "paperSrc",
    "pgBorders",
    "lnNumType",
    "pgNumType",
    "cols",
    "col",
    "formProt",
    "titlePg",
    "docGrid",
    "printerSettings",
    "bidi",
    "rtlGutter",
    "footnotePr",
    "endnotePr",
    "numFmt",
    "numStart",
    "numRestart",
    "suppressLineNumbers",
    "adjustRightInd",
    "mirrorIndents",
    "suppressOverlap",
    "kinsoku",
    "wordWrap",
    "overflowPunct",
    "topLinePunct",
    "autoSpaceDE",
    "autoSpaceDN",
}


def validate_structure(parts: dict[str, bytes]) -> None:
    """Reject unknown body semantics and text in secondary Word stories."""
    document = parse_xml(parts[main_document_name(parts)])
    if document.tag != W + "document" or len(document) != 1 or document[0].tag != W + "body":
        raise DocumentError(422, "unsupported_structure")
    _validate_node(document)
    for name in xml_part_names(parts):
        root = parse_xml(parts[name])
        if root.tag in {
            W + value for value in ("hdr", "ftr", "footnotes", "endnotes", "comments")
        } or (
            name.startswith("word/")
            and any(
                story in name for story in ("header", "footer", "footnotes", "endnotes", "comments")
            )
        ):
            _validate_empty_story(root)


def _validate_empty_story(root: Element) -> None:
    names = (
        _PROPERTIES
        | {"hdr", "ftr", "footnotes", "endnotes", "comments", "footnote", "endnote", "comment"}
        | {name for children in _CHILDREN.values() for name in children}
    )
    allowed = {W + name for name in names - {"document", "body"}}
    if any(
        node.tag not in allowed or (node.text or "").strip() or (node.tail or "").strip()
        for node in root.iter()
    ):
        raise DocumentError(422, "unsupported_structure")


def _validate_node(node: Element) -> None:
    name = node.tag.removeprefix(W)
    if name in {"pPr", "rPr", "tblPr", "trPr", "tcPr", "sectPr"}:
        if any(
            child.tag.removeprefix(W) not in _PROPERTIES
            or (child.text or "").strip()
            or (child.tail or "").strip()
            for child in node.iter()
        ):
            raise DocumentError(422, "unsupported_structure")
        return
    allowed = _CHILDREN.get(name, set())
    if (node.text or "").strip() and name != "t":
        raise DocumentError(422, "unsupported_structure")
    if name == "br" and node.get(W + "type", "textWrapping") != "textWrapping":
        raise DocumentError(422, "unsupported_structure")
    for child in node:
        if child.tag not in {W + value for value in allowed} or (child.tail or "").strip():
            raise DocumentError(422, "unsupported_structure")
        _validate_node(child)
    if name == "tbl":
        rows = node.findall(W + "tr")
        widths = {len(row.findall(W + "tc")) for row in rows}
        grid = node.find(W + "tblGrid")
        if not rows or len(widths) != 1 or grid is None or widths != {len(grid)}:
            raise DocumentError(422, "unsupported_structure")
