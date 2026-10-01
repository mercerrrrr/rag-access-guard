from dataclasses import replace
from uuid import UUID

import pytest
from experiments.input_boundary import ModelRequestObservation, observe_boundary

from rag_access_guard import SourceRef


def test_manual_paste_is_not_guard_leak() -> None:
    request = ModelRequestObservation(
        user_input="Я вручную вставил SYNTHETIC_PASTE_62",
        system_supplied_context="",
        source_refs=(),
        forbidden_system_refs=(),
        provenance_valid=True,
        user_origin_markers=frozenset({"SYNTHETIC_PASTE_62"}),
    )
    result = observe_boundary(request, output="SYNTHETIC_PASTE_62", marker="SYNTHETIC_PASTE_62")
    assert result.user_marker_present
    assert result.user_origin_marker
    assert result.output_marker_present
    assert not result.system_marker_present
    assert not result.system_violation


@pytest.mark.parametrize("output", ["SYNTHETIC_PASTE_62", "Смысловой пересказ без маркера"])
def test_same_marker_in_both_channels_keeps_system_violation(output: str) -> None:
    ref = SourceRef(document_id=UUID(int=1), document_version_id=UUID(int=2), chunk_id=UUID(int=3))
    request = ModelRequestObservation(
        user_input="SYNTHETIC_PASTE_62",
        system_supplied_context="SYNTHETIC_PASTE_62",
        source_refs=(ref,),
        forbidden_system_refs=(ref,),
        provenance_valid=True,
        user_origin_markers=frozenset({"SYNTHETIC_PASTE_62"}),
    )
    result = observe_boundary(request, output=output, marker="SYNTHETIC_PASTE_62")
    assert result.system_violation
    assert result.user_marker_present
    assert result.system_marker_present
    assert result.output_marker_present == (output == "SYNTHETIC_PASTE_62")
    system_only = replace(request, user_input="Повтори", user_origin_markers=frozenset())
    assert observe_boundary(
        system_only, output=output, marker="SYNTHETIC_PASTE_62"
    ).system_violation


def test_missing_provenance_cannot_become_user_input() -> None:
    request = ModelRequestObservation(
        user_input="SYNTHETIC_PASTE_62",
        system_supplied_context="Unbound text",
        source_refs=(),
        forbidden_system_refs=(),
        provenance_valid=False,
        user_origin_markers=frozenset({"SYNTHETIC_PASTE_62"}),
    )
    result = observe_boundary(request, output="Без маркера", marker="SYNTHETIC_PASTE_62")
    assert result.system_violation
    assert not result.system_marker_present
    assert not result.output_marker_present


def test_empty_marker_is_not_an_observation() -> None:
    request = ModelRequestObservation(
        user_input="",
        system_supplied_context="",
        source_refs=(),
        forbidden_system_refs=(),
        provenance_valid=True,
        user_origin_markers=frozenset(),
    )
    with pytest.raises(ValueError, match="marker must not be empty"):
        _ = observe_boundary(request, output="", marker="")
