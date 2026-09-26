"""Versioned serialization of system-supplied chunks, without user input."""

import json
from collections.abc import Callable
from hashlib import sha256

from rag_access_guard.types import CandidateChunk, PolicySnapshot, PreparedContext

type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None


def _decode(decoder: Callable[[str], JsonValue], text: str) -> JsonValue:
    """Give the stdlib JSON boundary its recursive value type without widening callers."""
    return decoder(text)


def render(chunks: tuple[CandidateChunk, ...]) -> str:
    """Encode untrusted document text as data, preserving chunk boundaries."""
    return json.dumps(
        {
            "version": 1,
            "chunks": [
                [
                    str(c.source_ref.document_id),
                    str(c.source_ref.document_version_id),
                    str(c.source_ref.chunk_id),
                    c.text,
                ]
                for c in chunks
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def matches_canonical(prepared: PreparedContext, snapshot: PolicySnapshot) -> bool:  # noqa: PLR0911
    """Check the serialized text against canonical hashes, not caller-supplied hashes."""
    try:
        value = _decode(json.loads, prepared.model_context)
    except (ValueError, RecursionError):
        return False
    if not isinstance(value, dict) or set(value) != {"version", "chunks"} or value["version"] != 1:
        return False
    if (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        != prepared.model_context
    ):
        return False
    entries = value["chunks"]
    if not isinstance(entries, list) or len(entries) != len(prepared.source_refs):
        return False
    hashes = dict(snapshot.canonical_chunk_hashes)
    for ref, entry in zip(prepared.source_refs, entries, strict=True):
        if not isinstance(entry, list) or len(entry) != 4:  # noqa: PLR2004 -- three IDs and text.
            return False
        content = entry[3]
        if not isinstance(content, str) or entry[:3] != [
            str(ref.document_id),
            str(ref.document_version_id),
            str(ref.chunk_id),
        ]:
            return False
        if sha256(content.encode("utf-8")).hexdigest() != hashes.get(ref):
            return False
    return True
