"""Project authorized canonical bodies into ordinal passages without changing provenance."""

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, assert_never

from rag_access_guard_api.schemas.evidence_context import (
    CONTEXT,
    ORIGINS,
    EvidencePriorTurn,
    EvidenceRef,
    HistoricalEvidence,
    SingleTurnEvidence,
)
from rag_access_guard_api.schemas.generation import GenerationUnavailable

if TYPE_CHECKING:
    from pydantic import JsonValue

ORIGIN_MARKER: Final = "\norigin_annotations_v1:\n"
MIN_BODY_CHARACTERS: Final = 150
UNKNOWN_LABEL: Final = "Источник; происхождение издателя не указано."
HISTORY_LABEL: Final = "Из ранее сохранённого ответа."


@dataclass(frozen=True, slots=True)
class EvidencePassage:
    """A context substring with host-owned origin labels."""

    text: str = field(repr=False)
    labels: tuple[str, ...]
    previous_question: str | None = field(default=None, repr=False)


def passages(text: str) -> tuple[str, ...]:
    """Keep source characters intact while attaching short headings to their body."""
    parts = re.split(r"(\n\s*\n|\n(?=\s*\d+(?:\.\d+)+[.]?\s))", text)
    result: list[str] = []
    carry = ""
    for index, part in enumerate(parts):
        carry += part
        if index % 2 == 0 and len(part.strip()) >= MIN_BODY_CHARACTERS:
            result.append(carry.strip())
            carry = ""
    if carry.strip():
        result.append(carry.strip())
    return tuple(result)


def evidence_passages(context: str) -> tuple[EvidencePassage, ...]:
    """Accept only the host's known context versions and annotations for present refs."""
    if not context:
        return ()
    raw, marker, origins = context.rpartition(ORIGIN_MARKER)
    value = CONTEXT.validate_json(raw if marker else context)
    match value:
        case SingleTurnEvidence():
            history: tuple[EvidencePriorTurn, ...] = ()
        case HistoricalEvidence():
            history = value.history
        case _:
            assert_never(value)
    refs = {(row[0], row[1], row[2]) for row in value.chunks}
    refs.update(ref for turn in history for ref in turn.source_refs)
    labels: dict[EvidenceRef, str] = {}
    for document, version, chunk, _, label in ORIGINS.validate_json(origins) if marker else ():
        ref = (document, version, chunk)
        if ref not in refs or ref in labels:
            raise GenerationUnavailable
        labels[ref] = label.value
    result: list[EvidencePassage] = []
    for document, version, chunk, text in value.chunks:
        source_labels = (labels.get((document, version, chunk), UNKNOWN_LABEL),)
        result.extend(EvidencePassage(part, source_labels) for part in passages(text))
    for turn in history:
        source_labels = (
            HISTORY_LABEL,
            *dict.fromkeys(labels.get(ref, UNKNOWN_LABEL) for ref in turn.source_refs),
        )
        result.extend(
            EvidencePassage(part, source_labels, turn.user_input) for part in passages(turn.answer)
        )
    return tuple(dict.fromkeys(result))


def model_passages(entries: tuple[EvidencePassage, ...]) -> str:
    """Expose display ordinals, never source IDs or arbitrary user input as quotations."""
    rows: list[dict[str, JsonValue]] = []
    for index, entry in enumerate(entries):
        row: dict[str, JsonValue] = {"id": index, "text": entry.text}
        if entry.previous_question is not None:
            row["previous_question"] = entry.previous_question
        rows.append(row)
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
