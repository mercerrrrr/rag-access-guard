"""Read OOXML parts without trusting declared expansion sizes or extracting files."""

import posixpath
import stat
import struct
import unicodedata
import zlib
from io import BytesIO
from typing import Final, NoReturn
from urllib.parse import unquote
from xml.etree import ElementTree as ET
from xml.parsers import expat
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

from rag_access_guard_api.adapters import docx_protocol as limits
from rag_access_guard_api.services.text_documents import DocumentError

_LOCAL_HEADER_BYTES: Final = 30
_RELATIONSHIPS: Final = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_CONTENT_TYPES: Final = "{http://schemas.openxmlformats.org/package/2006/content-types}"
_OFFICE_DOCUMENT: Final = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
)
_MAIN_TYPE: Final = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)


def _reject_declaration(*_args: str | int | None) -> NoReturn:
    raise DocumentError(422, "parse_failed")


def parse_xml(data: bytes) -> ET.Element:
    """Reject declarations through XML semantics before building an element tree."""
    parser = expat.ParserCreate()
    parser.StartDoctypeDeclHandler = _reject_declaration
    parser.EntityDeclHandler = _reject_declaration
    parser.ExternalEntityRefHandler = _reject_declaration
    try:
        _ = parser.Parse(data, True)  # noqa: FBT003 -- expat uses a positional-only final flag
        return ET.fromstring(data)  # noqa: S314 -- declarations rejected by the first parser
    except (expat.ExpatError, ET.ParseError):
        raise DocumentError(422, "parse_failed") from None


def read_parts(data: bytes) -> dict[str, bytes]:
    """Validate names, metadata and actual decompression before library parsing."""
    parts: dict[str, bytes] = {}
    normalized: set[str] = set()
    total = 0
    with ZipFile(BytesIO(data)) as archive:
        entries = archive.infolist()
        if (
            len(entries) > limits.MAX_ENTRIES
            or sum(e.file_size for e in entries) > limits.MAX_EXPANDED_BYTES
        ):
            raise DocumentError(422, "parse_failed")
        for entry in entries:
            name = entry.filename
            decoded = unquote(name)
            folded = unicodedata.normalize("NFC", decoded.rstrip("/")).casefold()
            if (
                not name
                or "\\" in decoded
                or "\x00" in decoded
                or ":" in decoded
                or decoded.startswith("/")
                or any(p in ("", ".", "..") for p in decoded.rstrip("/").split("/"))
                or folded in normalized
                or stat.S_ISLNK(entry.external_attr >> 16)
                or entry.flag_bits & 1
                or entry.file_size > limits.MAX_ENTRY_BYTES
                or entry.file_size > max(1, entry.compress_size) * limits.MAX_COMPRESSION_RATIO
            ):
                raise DocumentError(422, "parse_failed")
            normalized.add(folded)
            value = read_entry(data, entry, limits.MAX_EXPANDED_BYTES - total)
            total += len(value)
            if total > limits.MAX_EXPANDED_BYTES:
                raise DocumentError(422, "parse_failed")
            if name.lower().endswith(
                (".zip", ".docx", ".docm", ".xlsx", ".pptx", ".bin")
            ) or value.startswith(
                (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
            ):
                raise DocumentError(422, "unsupported_structure")
            if name.endswith((".xml", ".rels")) or name == "[Content_Types].xml":
                _ = parse_xml(value)
            parts[name] = value
    if not {"[Content_Types].xml", "_rels/.rels"} <= parts.keys():
        raise DocumentError(422, "parse_failed")
    _validate_package(parts)
    return parts


def main_document_name(parts: dict[str, bytes]) -> str:
    """Bind body validation to the unambiguous main part selected by python-docx."""
    root = parse_xml(parts["_rels/.rels"])
    ids = [rel.get("Id", "") for rel in root]
    main = [rel for rel in root if rel.get("Type") == _OFFICE_DOCUMENT]
    if (
        root.tag != _RELATIONSHIPS + "Relationships"
        or any(rel.tag != _RELATIONSHIPS + "Relationship" for rel in root)
        or not all(ids)
        or len(ids) != len(set(ids))
        or len(main) != 1
    ):
        raise DocumentError(422, "parse_failed")
    selected = main[0]
    name = selected.get("Target", "")
    if (
        selected.get("TargetMode", "Internal") != "Internal"
        or name != posixpath.normpath(name)
        or name != unquote(name)
        or name not in parts
    ):
        raise DocumentError(422, "parse_failed")
    types = parse_xml(parts["[Content_Types].xml"])
    overrides = [
        item
        for item in types
        if item.tag == _CONTENT_TYPES + "Override" and item.get("PartName") == "/" + name
    ]
    if (
        types.tag != _CONTENT_TYPES + "Types"
        or len(overrides) != 1
        or overrides[0].get("ContentType") != _MAIN_TYPE
    ):
        raise DocumentError(422, "parse_failed")
    return name


def xml_part_names(parts: dict[str, bytes]) -> set[str]:
    """Respect OPC content types even when XML parts have unconventional names."""
    types = parse_xml(parts["[Content_Types].xml"])
    defaults = {
        item.get("Extension", ""): item.get("ContentType", "")
        for item in types
        if item.tag.endswith("}Default")
    }
    overrides = {
        item.get("PartName", "").removeprefix("/"): item.get("ContentType", "")
        for item in types
        if item.tag.endswith("}Override")
    }
    return {
        name
        for name in parts
        if name.endswith((".xml", ".rels"))
        or overrides.get(name, defaults.get(name.rsplit(".", 1)[-1], ""))
        .lower()
        .endswith(("+xml", "/xml"))
    }


def read_entry(data: bytes, entry: ZipInfo, remaining: int) -> bytes:
    """Bound raw expansion independently of untrusted ZIP size declarations."""
    start = entry.header_offset
    header = data[start : start + _LOCAL_HEADER_BYTES]
    if len(header) != _LOCAL_HEADER_BYTES or header[:4] != b"PK\x03\x04":
        raise DocumentError(422, "parse_failed")
    fields = struct.unpack("<4s5H3I2H", header)
    name_length, extra_length = fields[-2:]
    name_start = start + _LOCAL_HEADER_BYTES
    name = data[name_start : name_start + name_length].decode(
        "utf-8" if entry.flag_bits & 2048 else "cp437"
    )
    if name != entry.filename or fields[2] != entry.flag_bits or fields[3] != entry.compress_type:
        raise DocumentError(422, "parse_failed")
    offset = name_start + name_length + extra_length
    compressed = data[offset : offset + entry.compress_size]
    if len(compressed) != entry.compress_size:
        raise DocumentError(422, "parse_failed")
    if entry.compress_type == ZIP_STORED:
        value = compressed
    elif entry.compress_type == ZIP_DEFLATED:
        inflater = zlib.decompressobj(-15)
        output = bytearray()
        budget = min(limits.MAX_ENTRY_BYTES, remaining)
        for pos in range(0, len(compressed), 65536):
            output.extend(
                inflater.decompress(compressed[pos : pos + 65536], budget + 1 - len(output))
            )
            if len(output) > budget or inflater.unconsumed_tail:
                raise DocumentError(422, "parse_failed")
        if not inflater.eof or inflater.unused_data:
            raise DocumentError(422, "parse_failed")
        value = bytes(output)
    else:
        raise DocumentError(422, "parse_failed")
    if (
        len(value) != entry.file_size
        or len(value) > min(limits.MAX_ENTRY_BYTES, remaining)
        or len(value) > max(1, entry.compress_size) * limits.MAX_COMPRESSION_RATIO
        or zlib.crc32(value) != entry.CRC
    ):
        raise DocumentError(422, "parse_failed")
    return value


def _validate_package(parts: dict[str, bytes]) -> None:
    for name in xml_part_names(parts):
        _ = parse_xml(parts[name])
    types = parse_xml(parts["[Content_Types].xml"])
    for item in types:
        content_type = item.get("ContentType", "").lower()
        if any(word in content_type for word in ("macroenabled", "vba", "oleobject")):
            raise DocumentError(422, "unsupported_structure")
    for name, data in parts.items():
        if not name.endswith(".rels"):
            continue
        for rel in parse_xml(data):
            _validate_relationship(name, rel, parts)


def _validate_relationship(name: str, rel: ET.Element, parts: dict[str, bytes]) -> None:
    kind = rel.get("Type", "").rsplit("/", 1)[-1].lower()
    if kind in {"image", "oleobject", "package", "afchunk", "vbaproject"}:
        raise DocumentError(422, "unsupported_structure")
    if rel.get("TargetMode") == "External":
        if kind != "hyperlink":
            raise DocumentError(422, "unsupported_structure")
        return
    target = unquote(rel.get("Target", ""))
    base = posixpath.dirname(posixpath.dirname(name)) if name != "_rels/.rels" else ""
    resolved = posixpath.normpath(posixpath.join(base, target))
    if (
        target.startswith("/")
        or "\\" in target
        or resolved.startswith("../")
        or resolved not in parts
    ):
        raise DocumentError(422, "parse_failed")
