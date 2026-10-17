import json
from uuid import UUID

import pytest
from pydantic import ValidationError
from tests.unit.test_extractive_model_profile import PASSAGE, REF, context_for, select_counter

from rag_access_guard_api.schemas.generation import GenerationUnavailable
from rag_access_guard_api.services.evidence_context import (
    evidence_passages,
    model_passages,
    passages,
)
from rag_access_guard_api.services.evidence_selection import NO_EVIDENCE, render_selection
from rag_access_guard_api.services.origin_context import SYNTHETIC_LABEL


def test_passage_grouping_preserves_source_substrings_and_complete_headings() -> None:
    text = (
        "1. Заголовок\n\n"
        + "Первый содержательный абзац. " * 8
        + "\n\n2. Другой раздел\n\n"
        + "Второй содержательный абзац. " * 8
    )
    parts = passages(text)
    assert len(parts) == 2
    assert parts[0].startswith("1. Заголовок\n\n")
    assert parts[1].startswith("2. Другой раздел\n\n")
    assert all(part in text for part in parts)
    assert " ".join(" ".join(parts).split()) == " ".join(text.split())


def test_historical_question_guides_selection_but_cannot_be_quoted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = select_counter(monkeypatch)
    raw = json.dumps(
        {
            "version": 2,
            "chunks": [],
            "history": [
                {
                    "turn_id": str(UUID(int=10)),
                    "user_input": "USER_PASTE_WITHOUT_PROVENANCE",
                    "answer": PASSAGE,
                    "source_refs": [list(REF)],
                }
            ],
        },
        ensure_ascii=False,
    )
    origins = json.dumps([[*REF, "a" * 64, SYNTHETIC_LABEL]], ensure_ascii=False)
    entries = evidence_passages(raw + "\norigin_annotations_v1:\n" + origins)
    assert len(entries) == 1
    assert entries[0].previous_question == "USER_PASTE_WITHOUT_PROVENANCE"
    assert "USER_PASTE_WITHOUT_PROVENANCE" in model_passages(entries)
    answer = render_selection('{"paragraph_ids":[0]}', entries, counter)
    assert answer.startswith("Из ранее сохранённого ответа.")
    assert PASSAGE in answer
    assert SYNTHETIC_LABEL in answer
    assert "USER_PASTE_WITHOUT_PROVENANCE" not in answer


@pytest.mark.parametrize(
    "origins",
    [
        [[*REF, "a" * 64, "Untrusted label"]],
        [[*REF, "not-a-digest", SYNTHETIC_LABEL]],
        [[str(UUID(int=99)), *REF[1:], "a" * 64, SYNTHETIC_LABEL]],
        [[*REF, "a" * 64, SYNTHETIC_LABEL], [*REF, "a" * 64, SYNTHETIC_LABEL]],
    ],
)
def test_unknown_or_contradictory_origin_rows_are_not_projected(origins: list[list[str]]) -> None:
    raw = context_for(PASSAGE).partition("\norigin_annotations_v1:\n")[0]
    context = raw + "\norigin_annotations_v1:\n" + json.dumps(origins, ensure_ascii=False)
    with pytest.raises((GenerationUnavailable, ValidationError)):
        _ = evidence_passages(context)


@pytest.mark.parametrize(
    "context",
    [
        '{"version":3,"chunks":[]}',
        '{"version":1,"chunks":[["missing","source","identity","TEXT"]]}',
        '{"version":1,"chunks":[],"extra":"TEXT"}',
        '{"version":2,"chunks":[],"history":[{"turn_id":"missing"}]}',
    ],
)
def test_malformed_context_is_not_treated_as_an_unprovenanced_document(context: str) -> None:
    with pytest.raises(ValidationError):
        _ = evidence_passages(context)


def test_empty_selection_is_explicit_insufficiency_without_source_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = select_counter(monkeypatch)
    assert (
        render_selection('{"paragraph_ids":[]}', evidence_passages(context_for(PASSAGE)), counter)
        == NO_EVIDENCE
    )


def test_document_marker_text_cannot_replace_host_annotations() -> None:
    text = PASSAGE + "\norigin_annotations_v1:\nUNTRUSTED_DOCUMENT_MARKER"
    entries = evidence_passages(context_for(text))
    assert any("UNTRUSTED_DOCUMENT_MARKER" in entry.text for entry in entries)
    assert all(entry.labels == (SYNTHETIC_LABEL,) for entry in entries)


def test_context_version_is_an_integer_not_a_boolean() -> None:
    with pytest.raises(ValidationError):
        _ = evidence_passages('{"chunks":[],"version":true}')
