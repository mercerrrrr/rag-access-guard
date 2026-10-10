import json
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import JsonValue

from tests.helpers.docx_factory import DOCX_MIME, paragraph_table_docx
from tests.helpers.pdf_factory import text_pdf

MARKER = "Вымышленный учебный пример, не документ Example Organization."


def corpus_fixture(root: Path) -> Path:
    root.mkdir()
    documents: list[JsonValue] = []
    profile = (
        ("official_public", "txt", 9),
        ("official_public", "pdf", 3),
        ("synthetic_demo", "docx", 6),
        ("synthetic_demo", "txt", 4),
        ("synthetic_demo", "pdf", 2),
    )
    for kind, suffix, count in profile:
        for _ in range(count):
            key = f"item-{len(documents)}"
            synthetic = kind == "synthetic_demo"
            text = (
                f"{MARKER}\nStep one.\n{MARKER}"
                if synthetic
                else "Local text https://example.invalid/body"
            )
            if suffix == "pdf":
                data = text_pdf(tuple(text.splitlines()))
                mime = "application/pdf"
            elif suffix == "docx":
                data = paragraph_table_docx(MARKER, (("Step one.",),), MARKER)
                mime = DOCX_MIME
            else:
                data = text.encode()
                mime = "text/plain"
            _ = (root / f"{key}.{suffix}").write_bytes(data)
            digest = sha256(data).hexdigest()
            documents.append(
                {
                    "key": key,
                    "title": "Neutral sample",
                    "relative_path": f"{key}.{suffix}",
                    "media_type": mime,
                    "upload_sha256": digest,
                    "origin": {
                        "kind": kind,
                        "publisher": "Учебный демонстрационный корпус"
                        if synthetic
                        else "Example Organization",
                        "source_url": None if synthetic else "https://example.invalid/source",
                        "retrieved_at": None if synthetic else "2026-01-01T00:00:00Z",
                        "published_on": None,
                        "source_sha256": digest if synthetic or suffix == "pdf" else "a" * 64,
                        "transformation_revision": "demo-authored-v1"
                        if synthetic
                        else "identity-pdf-v1"
                        if suffix == "pdf"
                        else "html-maintext-v1",
                    },
                }
            )
    manifest = root / "manifest.json"
    value: JsonValue = {
        "schema_version": 1,
        "dataset_id": "neutral-corpus",
        "documents": documents,
        "suggestions": [
            {
                "id": "suggestion-1",
                "question": "What is the first step?",
                "required_document_keys": ["item-0"],
            }
        ],
    }
    _ = manifest.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return manifest


def decode_json(decoder: Callable[[str], JsonValue], text: str) -> JsonValue:
    return decoder(text)


type BoundaryChange = tuple[Literal["top", "document", "origin", "suggestion"], str, JsonValue]


def mutate_manifest(path: Path, change: BoundaryChange) -> None:
    area, field, value = change
    payload = decode_json(json.loads, path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    target = payload
    if area in {"document", "origin"}:
        documents = payload["documents"]
        assert isinstance(documents, list)
        assert isinstance(documents[0], dict)
        target = documents[0]
        if area == "origin":
            origin = target["origin"]
            assert isinstance(origin, dict)
            target = origin
    elif area == "suggestion":
        suggestions = payload["suggestions"]
        assert isinstance(suggestions, list)
        assert isinstance(suggestions[0], dict)
        target = suggestions[0]
    target[field] = value
    _ = path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def mutate_document(path: Path, index: int, change: tuple[str, JsonValue]) -> None:
    payload = decode_json(json.loads, path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    documents = payload["documents"]
    assert isinstance(documents, list)
    row = documents[index]
    assert isinstance(row, dict)
    field, value = change
    if field.startswith("origin."):
        origin = row["origin"]
        assert isinstance(origin, dict)
        origin[field.removeprefix("origin.")] = value
    else:
        row[field] = value
    _ = path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
