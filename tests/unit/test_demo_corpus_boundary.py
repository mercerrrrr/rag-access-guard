import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from tests.helpers.demo_corpus_factory import BoundaryChange, corpus_fixture, mutate_manifest

from rag_access_guard_api.services.demo_corpus import CorpusValidationError, load_manifest


@pytest.fixture
def manifest(tmp_path: Path) -> Path:
    return corpus_fixture(tmp_path / "corpus")


@pytest.mark.parametrize(
    "change",
    [
        ("top", "schema_version", 2),
        ("top", "schema_version", True),
        ("top", "dataset_id", ""),
        ("top", "dataset_id", " "),
        ("top", "unexpected", 1),
        ("top", "documents", []),
        ("document", "title", ""),
        ("document", "title", "x" * 301),
        ("document", "key", ""),
        ("document", "key", 12),
        ("document", "upload_sha256", "A" * 64),
        ("document", "origin", None),
        ("document", "original_relative_path", "original.html"),
        ("document", "media_type", "text/markdown"),
        ("origin", "source_sha256", "bad"),
        ("origin", "kind", "user_upload"),
        ("origin", "retrieved_at", 0),
        ("suggestion", "id", ""),
        ("suggestion", "question", " "),
        ("suggestion", "required_document_keys", []),
        ("suggestion", "required_document_keys", ["unknown"]),
        ("suggestion", "required_document_keys", ["item-0", "item-0"]),
    ],
)
def test_boundary_rejects_invalid_data(manifest: Path, change: BoundaryChange) -> None:
    # Given / When / Then: accepting the changed boundary value is the regression.
    mutate_manifest(manifest, change)
    with pytest.raises(ValidationError):
        _ = load_manifest(manifest)


@pytest.mark.parametrize(
    "path",
    [
        "../outside.txt",
        "/absolute.txt",
        "C:/absolute.txt",
        "C:drive.txt",
        "\\\\server\\share\\file.txt",
        "folder\\..\\outside.txt",
        "folder\\inside.txt",
        "//server/share/file.txt",
        "a/../file.txt",
        "a//file.txt",
        "./file.txt",
        "file.txt:stream",
        "folder./file.txt",
        "folder /file.txt",
    ],
)
def test_boundary_rejects_portable_path_escape(manifest: Path, path: str) -> None:
    # Given / When / Then: path syntax must fail before reading any bytes.
    mutate_manifest(manifest, ("document", "relative_path", path))
    with pytest.raises(ValidationError):
        _ = load_manifest(manifest)


@pytest.mark.parametrize(
    "area", ["document-key", "document-path", "path-case", "suggestion", "counts"]
)
def test_boundary_rejects_duplicate_or_wrong_profile(manifest: Path, area: str) -> None:
    # Given
    text = manifest.read_text(encoding="utf-8")
    if area == "document-key":
        text = text.replace('"key": "item-1"', '"key": "item-0"')
    elif area == "document-path":
        text = text.replace('"relative_path": "item-1.txt"', '"relative_path": "item-0.txt"')
    elif area == "path-case":
        text = text.replace('"relative_path": "item-1.txt"', '"relative_path": "ITEM-0.TXT"')
    elif area == "suggestion":
        duplicate = json.dumps(
            {"id": "suggestion-1", "question": "Other?", "required_document_keys": ["item-1"]}
        )
        text = text.replace(
            '"suggestions": [',
            f'"suggestions": [{duplicate},',
        )
    else:
        text = text.replace('"media_type": "text/plain"', '"media_type": "application/pdf"', 1)
    _ = manifest.write_text(text, encoding="utf-8")
    # When / Then
    with pytest.raises(ValidationError):
        _ = load_manifest(manifest)


@pytest.mark.parametrize("member", ["schema_version", "dataset_id", "key", "source_sha256"])
def test_duplicate_json_members_are_rejected(manifest: Path, member: str) -> None:
    # Given duplicate top-level or nested members with identical valid values.
    text = manifest.read_text(encoding="utf-8")
    position = text.index(f'"{member}"')
    end = text.index(",", position)
    text = text[:position] + text[position:end] + "," + text[position:]
    _ = manifest.write_text(text, encoding="utf-8")
    # When / Then
    with pytest.raises(CorpusValidationError, match="duplicate_json_key"):
        _ = load_manifest(manifest)


def test_manifest_is_deeply_frozen(manifest: Path) -> None:
    # Given a parsed boundary; mutating a nested field would bypass validation.
    model = load_manifest(manifest)
    # When / Then
    with pytest.raises(ValidationError):
        model.documents[0].title = "changed"


@pytest.mark.parametrize(
    "raw",
    [b"{", b"\xff", b"", b"x" * 10_485_761],
    ids=["malformed", "encoding", "empty", "oversize"],
)
def test_manifest_is_bounded_and_rejects_malformed_bytes(tmp_path: Path, raw: bytes) -> None:
    # Given malformed, empty, or over-limit local input.
    path = tmp_path / "manifest.json"
    _ = path.write_bytes(raw)
    # When / Then
    with pytest.raises((ValueError, UnicodeError, CorpusValidationError)):
        _ = load_manifest(path)
