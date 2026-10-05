from dataclasses import replace
from datetime import UTC, datetime
from json import dumps
from pathlib import Path
from uuid import UUID

import pytest
from experiments.input_boundary import ModelRequestObservation
from experiments.observations import ArmRecord, ArmResult, AttemptEvidence
from experiments.reproduction_outcomes import normalized_arm
from experiments.trials import TrialRecord
from experiments.verify_reproduction import assert_trial_outcomes
from tests.experiments.test_summary_aggregation import summary_input
from tests.experiments.test_surface_metrics import release

from rag_access_guard import SourceRef
from rag_access_guard_api.schemas.chat import PendingTurn, ThreadDetail, UnavailableTurn


def changed(record: TrialRecord, *, available: bool) -> TrialRecord:
    assert record.pair is not None
    guarded = replace(record.pair.guarded, observations=(release("ask", available=available),))
    return record.model_copy(
        update={
            "pair": replace(record.pair, guarded=guarded),
            "arms": (record.arms[0], ArmRecord(status="completed", result=guarded)),
        }
    )


def test_swapped_repetition_outcomes_are_not_reproduced(tmp_path: Path) -> None:
    _, first, _ = summary_input(tmp_path)
    second = first.model_copy(
        update={"trial": first.trial.model_copy(update={"trial_id": "allowed.1", "repetition": 1})}
    )
    original = (changed(first, available=True), changed(second, available=False))
    repeated = (changed(first, available=False), changed(second, available=True))
    with pytest.raises(ValueError, match=r"[Rr]eprodu"):
        assert_trial_outcomes(original, repeated)


def test_attempt_decision_change_is_not_reproduced(tmp_path: Path) -> None:
    _, record, _ = summary_input(tmp_path)
    arm = record.arms[1].result
    attempt = AttemptEvidence(
        action_id="ask", number=1, status="completed", elapsed_ms=1, stage_ms=(), model_called=True
    )
    altered = record.model_copy(
        update={
            "arms": (
                record.arms[0],
                ArmRecord(status="completed", result=replace(arm, attempts=(attempt,))),
            )
        }
    )
    with pytest.raises(ValueError, match=r"[Rr]eprodu"):
        assert_trial_outcomes((record,), (altered,))


def test_identical_trial_decisions_are_reproduced(tmp_path: Path) -> None:
    _, record, _ = summary_input(tmp_path)
    assert_trial_outcomes((record,), (record.model_copy(update={"elapsed_ms": 999}),))


def test_generated_ids_and_timings_do_not_change_decisions(tmp_path: Path) -> None:
    _, record, _ = summary_input(tmp_path)
    arm = record.arms[1].result
    normalized: list[ArmResult] = []
    for offset in (0, 100):
        ref = SourceRef(
            document_id=UUID(int=offset + 1),
            document_version_id=UUID(int=offset + 2),
            chunk_id=UUID(int=offset + 3),
        )
        context = dumps(
            {
                "chunks": [
                    [str(ref.document_id), str(ref.document_version_id), str(ref.chunk_id), "fixed"]
                ],
                "history": [{"turn_id": str(UUID(int=offset + 4)), "answer": "fixed"}],
            }
        )
        request = ModelRequestObservation(
            user_input="fixed",
            system_supplied_context=context,
            source_refs=(ref,),
            forbidden_system_refs=(),
            provenance_valid=True,
            user_origin_markers=frozenset(),
        )
        observation = replace(
            arm.observations[0], source_refs=(ref,), elapsed_ms=offset, request=request
        )
        normalized.append(
            normalized_arm(
                replace(arm, documents=(("public", ref.document_id),), observations=(observation,))
            )
        )
    assert normalized[0] == normalized[1]


@pytest.mark.parametrize("damage", ["provenance", "source", "attempt_state"])
def test_structural_observation_changes_are_rejected(tmp_path: Path, damage: str) -> None:
    _, record, _ = summary_input(tmp_path)
    arm = record.arms[1].result
    observation = arm.observations[0]
    if damage == "provenance":
        observation = replace(observation, provenance_valid=False)
    elif damage == "source":
        observation = replace(observation, source_refs=())
    else:
        observation = replace(observation, attempt_status="stale_discarded")
    altered = record.model_copy(
        update={
            "arms": (
                record.arms[0],
                ArmRecord(status="completed", result=replace(arm, observations=(observation,))),
            )
        }
    )
    with pytest.raises(ValueError, match=r"[Rr]eprodu"):
        assert_trial_outcomes((record,), (altered,))


def test_stored_read_states_are_compared_even_without_visible_content(tmp_path: Path) -> None:
    _, record, _ = summary_input(tmp_path)
    arm = record.arms[1].result
    projections: list[ArmResult] = []
    for turn_type in (PendingTurn, UnavailableTurn):
        detail = ThreadDetail(
            id=UUID(int=10),
            title="fixed",
            revision=1,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            turns=(turn_type(id=UUID(int=11), request_id=UUID(int=12), user_input="fixed"),),
        )
        observation = replace(
            release("read", available=False),
            surface="stored_read",
            persisted="unchanged",
            response_body=detail.model_dump_json(),
        )
        projections.append(normalized_arm(replace(arm, observations=(observation,))))
    assert projections[0] != projections[1]
