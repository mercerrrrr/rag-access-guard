"""Versioned serialization of chunks and whole historical pairs."""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Final
from uuid import UUID

from rag_access_guard._validation import require_nonnegative
from rag_access_guard.ports import TokenCounter
from rag_access_guard.types import (
    CandidateChunk,
    PolicySnapshot,
    PreparedContext,
    PriorTurn,
    SourceRef,
)

type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None
HISTORY_VERSION: Final = 2
RENDERER_REVISION: Final = "rag-access-guard/context/v2"


@dataclass(frozen=True, slots=True)
class BoundedContext:
    """Serialized data and its exact closure, without an authorization decision."""

    model_context: str = field(repr=False)
    source_refs: tuple[SourceRef, ...]


def _decode(decoder: Callable[[str], JsonValue], text: str) -> JsonValue:
    """Give the stdlib JSON boundary its recursive value type without widening callers."""
    return decoder(text)


def context_refs(
    chunks: tuple[CandidateChunk, ...], history: tuple[PriorTurn, ...]
) -> tuple[SourceRef, ...]:
    """Flatten only the sources actually present in the serialized context."""
    return tuple(
        dict.fromkeys(
            [c.source_ref for c in chunks] + [ref for turn in history for ref in turn.source_refs]
        )
    )


def render_context(chunks: tuple[CandidateChunk, ...], history: tuple[PriorTurn, ...]) -> str:
    """Encode untrusted document text as data, preserving chunk boundaries."""
    value: dict[str, JsonValue] = {
        "version": HISTORY_VERSION if history else 1,
        "chunks": [
            [
                str(c.source_ref.document_id),
                str(c.source_ref.document_version_id),
                str(c.source_ref.chunk_id),
                c.text,
            ]
            for c in chunks
        ],
    }
    if history:
        value["history"] = [
            {
                "turn_id": str(t.turn_id),
                "user_input": t.user_input,
                "answer": t.answer,
                "source_refs": [
                    [str(r.document_id), str(r.document_version_id), str(r.chunk_id)]
                    for r in t.source_refs
                ],
            }
            for t in history
        ]
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def bound_context(
    chunks: tuple[CandidateChunk, ...],
    history: tuple[PriorTurn, ...],
    *,
    token_counter: TokenCounter,
    max_context_tokens: int = 5000,
    max_prior_turns: int = 4,
) -> BoundedContext:
    """Trim whole serialized pairs before ranked chunks; callers must authorize inputs first."""
    require_nonnegative(max_context_tokens)
    require_nonnegative(max_prior_turns)
    kept_chunks = chunks
    kept_history = history[-max_prior_turns:] if max_prior_turns else ()
    while kept_chunks or kept_history:
        rendered = render_context(kept_chunks, kept_history)
        if token_counter.count(rendered) <= max_context_tokens:
            return BoundedContext(rendered, context_refs(kept_chunks, kept_history))
        if kept_history:
            kept_history = kept_history[1:]
        else:
            kept_chunks = kept_chunks[:-1]
    return BoundedContext("", ())


def _source_ref(value: JsonValue) -> SourceRef | None:
    if not isinstance(value, list) or len(value) != 3:  # noqa: PLR2004 -- complete source tuple.
        return None
    document, version, chunk = value
    if not isinstance(document, str) or not isinstance(version, str) or not isinstance(chunk, str):
        return None
    try:
        return SourceRef(
            document_id=UUID(document), document_version_id=UUID(version), chunk_id=UUID(chunk)
        )
    except ValueError:
        return None


def _history(entries: JsonValue) -> tuple[PriorTurn, ...] | None:  # noqa: PLR0911
    if not isinstance(entries, list):
        return None
    turns: list[PriorTurn] = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "turn_id",
            "user_input",
            "answer",
            "source_refs",
        }:
            return None
        turn_id, question, answer, refs = (
            entry[key] for key in ("turn_id", "user_input", "answer", "source_refs")
        )
        if (
            not isinstance(turn_id, str)
            or not isinstance(question, str)
            or not isinstance(answer, str)
            or not isinstance(refs, list)
            or not refs
        ):
            return None
        sources: list[SourceRef] = []
        try:
            for ref in refs:
                source = _source_ref(ref)
                if source is None:
                    return None
                sources.append(source)
            turns.append(
                PriorTurn(
                    turn_id=UUID(turn_id),
                    user_input=question,
                    answer=answer,
                    source_refs=tuple(sources),
                    provenance_complete=True,
                )
            )
        except ValueError:
            return None
    if len({t.turn_id for t in turns}) != len(turns):
        return None
    return tuple(turns)


def _context(text: str) -> dict[str, JsonValue] | None:
    try:
        value = _decode(json.loads, text)
    except (ValueError, RecursionError):
        return None
    if not isinstance(value, dict):
        return None
    if not (
        (set(value) == {"version", "chunks"} and value["version"] == 1)
        or (set(value) == {"version", "chunks", "history"} and value["version"] == HISTORY_VERSION)
    ):
        return None
    if json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) != text:
        return None
    return value


def matches_history(prepared: PreparedContext, canonical: tuple[PriorTurn, ...]) -> bool:
    """Let the host bind serialized historical bodies to its own canonical rows."""
    value = _context(prepared.model_context)
    if value is None:
        return False
    history = _history(value.get("history", []))
    if history is None:
        return False
    selected_ids = {t.turn_id for t in history}
    return history == tuple(t for t in canonical if t.turn_id in selected_ids)


def matches_canonical(prepared: PreparedContext, snapshot: PolicySnapshot) -> bool:  # noqa: PLR0911
    """Check the serialized text against canonical hashes, not caller-supplied hashes."""
    value = _context(prepared.model_context)
    if value is None:
        return False
    entries = value["chunks"]
    if not isinstance(entries, list):
        return False
    hashes = dict(snapshot.canonical_chunk_hashes)
    sources: list[SourceRef] = []
    for entry in entries:
        if not isinstance(entry, list) or len(entry) != 4:  # noqa: PLR2004 -- three IDs and text.
            return False
        content = entry[3]
        ref = _source_ref(entry[:3])
        if not isinstance(content, str) or ref is None:
            return False
        if sha256(content.encode("utf-8")).hexdigest() != hashes.get(ref):
            return False
        sources.append(ref)
    history = _history(value.get("history", []))
    if history is None:
        return False
    sources.extend(ref for turn in history for ref in turn.source_refs)
    return tuple(dict.fromkeys(sources)) == prepared.source_refs and all(
        ref in hashes for ref in sources
    )
