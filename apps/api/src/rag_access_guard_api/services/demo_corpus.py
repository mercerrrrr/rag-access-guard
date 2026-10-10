"""Bounded local inspection using the ordinary upload parsers, without ingestion."""

import json
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import BinaryIO, Final, Literal, override

from pydantic import JsonValue

from rag_access_guard_api.adapters.docx_parser import parse_docx
from rag_access_guard_api.adapters.pdf_parser import parse_pdf
from rag_access_guard_api.adapters.text_parser import parse_text
from rag_access_guard_api.schemas.demo_corpus import (
    CorpusCounts,
    CorpusDocument,
    CorpusDocumentReport,
    CorpusValidationReport,
    DemoCorpusManifest,
)
from rag_access_guard_api.schemas.ingestion import ParsedDocument, UploadPayload
from rag_access_guard_api.services.text_documents import MAX_TEXT_BYTES, DocumentError

SYNTHETIC_MARKER_PREFIX: Final = "Вымышленный учебный пример, не документ "
REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[5]


@dataclass(frozen=True, slots=True)
class CorpusValidationError(Exception):
    """A safe code that never includes private input text or host filesystem paths."""

    code: str

    @override
    def __str__(self) -> str:
        """Expose only the safe defect code at a caller boundary."""
        return self.code


def read_bounded(stream: BinaryIO) -> bytes:
    """Bound actual reads even if a file grows after its initial size check."""
    data = stream.read(MAX_TEXT_BYTES + 1)
    if not data or len(data) > MAX_TEXT_BYTES:
        raise CorpusValidationError(code="size_limit")
    return data


def _read_file(path: Path) -> bytes:
    try:
        with path.open("rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise CorpusValidationError(code="file_unavailable")
            if not 0 < metadata.st_size <= MAX_TEXT_BYTES:
                raise CorpusValidationError(code="size_limit")
            return read_bounded(stream)
    except OSError:
        raise CorpusValidationError(code="file_unavailable") from None


def _unique_members(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise CorpusValidationError(code="duplicate_json_key")
        result[key] = value
    return result


def _scan_json(decoder: Callable[..., JsonValue], data: bytes) -> None:
    _ = decoder(data, object_pairs_hook=_unique_members)


def _parse_manifest(data: bytes) -> DemoCorpusManifest:
    _scan_json(json.loads, data)
    return DemoCorpusManifest.model_validate_json(data, strict=True)


def load_manifest(path: Path) -> DemoCorpusManifest:
    """Reject duplicate JSON members before the strict shared-origin boundary."""
    return _parse_manifest(_read_file(path))


def _local_document(root: Path, relative_path: str) -> Path:
    candidate = root / relative_path
    try:
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise CorpusValidationError(code="path_escape")
        for ancestor in (candidate, *candidate.parents):
            if ancestor == root:
                break
            metadata = ancestor.lstat()
            if (
                stat.S_ISLNK(metadata.st_mode)
                or getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise CorpusValidationError(code="path_escape")
        if not resolved.is_file():
            raise CorpusValidationError(code="file_unavailable")
    except (OSError, RuntimeError):
        raise CorpusValidationError(code="file_unavailable") from None
    return resolved


def _inspect_document(root: Path, document: CorpusDocument) -> CorpusDocumentReport:
    path = _local_document(root, document.relative_path)
    formats: dict[str, tuple[str, Callable[[UploadPayload], ParsedDocument]]] = {
        ".txt": ("text/plain", parse_text),
        ".pdf": ("application/pdf", parse_pdf),
        ".docx": (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            parse_docx,
        ),
    }
    selected = formats.get(path.suffix.lower())
    if selected is None or document.media_type != selected[0]:
        raise CorpusValidationError(code="format_mismatch")
    data = _read_file(path)
    digest = sha256(data).hexdigest()
    if digest != document.upload_sha256:
        raise CorpusValidationError(code="upload_hash_mismatch")
    synthetic = document.origin.kind == "synthetic_demo"
    identity = synthetic or document.origin.transformation_revision == "identity-pdf-v1"
    if identity and digest != document.origin.source_sha256:
        raise CorpusValidationError(code="source_hash_mismatch")
    try:
        parsed = selected[1](
            UploadPayload(filename=path.name, media_type=document.media_type, data=data)
        )
    except DocumentError:
        raise CorpusValidationError(code="parse_failed") from None
    if synthetic:
        lines = [line for line in parsed.text.splitlines() if line.strip()]
        if (
            not lines
            or not lines[0].startswith(SYNTHETIC_MARKER_PREFIX)
            or lines[0] == SYNTHETIC_MARKER_PREFIX.strip()
            or lines[0] not in lines[1:]
        ):
            raise CorpusValidationError(code="synthetic_marker")
    kind: Literal["synthetic_demo", "official_public"] = (
        "synthetic_demo" if synthetic else "official_public"
    )
    return CorpusDocumentReport(
        key=document.key,
        kind=kind,
        media_type=document.media_type,
        upload_sha256=digest,
        source_sha256=document.origin.source_sha256,
        source_check="matched_upload" if identity else "not_checked_external_original",
        parser_revision=parsed.parser_revision,
        text_sha256=sha256(parsed.text.encode("utf-8")).hexdigest(),
        text_characters=len(parsed.text),
        synthetic_marker_checked=synthetic,
    )


def validate_corpus(path: Path) -> CorpusValidationReport:
    """Inspect all 24 local inputs; external original bytes require separate evidence."""
    raw = _read_file(path)
    manifest = _parse_manifest(raw)
    root = path.parent.resolve(strict=True)
    reports = tuple(_inspect_document(root, document) for document in manifest.documents)
    return CorpusValidationReport(
        dataset_id=manifest.dataset_id,
        manifest_sha256=sha256(raw).hexdigest(),
        documents=reports,
        counts=CorpusCounts(
            documents=24, official_public=12, synthetic_demo=12, txt=13, docx=6, pdf=5
        ),
    )


def validate_report_path(path: Path) -> Path:
    """Reject checkout paths and existing targets before any creation or parsing."""
    try:
        target = path.resolve()
        if target.is_relative_to(REPOSITORY_ROOT) or any(
            (parent / ".git").exists() for parent in target.parents
        ):
            raise CorpusValidationError(code="report_inside_git")
        if path.exists() or path.is_symlink():
            raise CorpusValidationError(code="report_exists")
        if not target.parent.is_dir():
            raise CorpusValidationError(code="report_parent_unavailable")
    except (OSError, RuntimeError):
        raise CorpusValidationError(code="report_unavailable") from None
    return target


def write_report(path: Path, report: CorpusValidationReport) -> None:
    """Create only the explicit private report, exclusively, without recovery writes."""
    target = validate_report_path(path)
    try:
        with target.open("x", encoding="utf-8") as stream:
            _ = stream.write(report.model_dump_json(indent=2) + "\n")
    except OSError:
        raise CorpusValidationError(code="report_unavailable") from None
