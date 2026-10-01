from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
from experiments.boundary_capture import CanonicalEvidence, capture_request, merge_user_origins
from experiments.input_boundary import ModelRequestObservation, observe_boundary

from rag_access_guard import (
    CandidateChunk,
    Guard,
    PolicyReader,
    PreparedContext,
    PrepareDenied,
    PriorTurn,
)
from rag_access_guard_api.adapters import llm
from rag_access_guard_api.schemas.chat import MessageRequest
from tests.integration.test_chat_history_boundaries import add_document
from tests.support.chat_generation import CapturingLLM, ChatCase


@dataclass
class BoundaryLLM:
    model: CapturingLLM
    prepared: list[tuple[PreparedContext, CanonicalEvidence]]
    requests: list[ModelRequestObservation] = field(default_factory=list)
    prior_origins: frozenset[str] = frozenset()

    async def generate(self, *, user_input: str, system_supplied_context: str) -> str:
        prepared, evidence = self.prepared[-1]
        origins = merge_user_origins(
            user_input=user_input,
            synthetic_markers=frozenset({"SYNTHETIC_PASTE_62"}),
            prior_origins=self.prior_origins,
        )
        self.requests.append(
            capture_request(
                user_input=user_input,
                system_supplied_context=system_supplied_context,
                prepared=prepared,
                evidence=evidence,
                user_origin_markers=origins,
            )
        )
        return await self.model.generate(
            user_input=user_input, system_supplied_context=system_supplied_context
        )


@pytest.fixture
def boundary_model(chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch) -> BoundaryLLM:
    records: list[tuple[PreparedContext, CanonicalEvidence]] = []
    original = Guard.prepare_context

    async def record(
        self: Guard,
        principal_id: UUID,
        candidate_chunks: tuple[CandidateChunk, ...],
        prior_turns: tuple[PriorTurn, ...],
        policy_reader: PolicyReader,
    ) -> PreparedContext | PrepareDenied:
        prepared = await original(self, principal_id, candidate_chunks, prior_turns, policy_reader)
        if isinstance(prepared, PreparedContext):
            snapshot = await policy_reader.snapshot(principal_id, prepared.source_refs)
            records.append((prepared, CanonicalEvidence(snapshot=snapshot, history=prior_turns)))
        return prepared

    model = BoundaryLLM(chat_case.model, records)
    monkeypatch.setattr(Guard, "prepare_context", record)
    monkeypatch.setattr(llm, "get_llm_adapter", lambda: model)
    return model


def test_historical_question_is_removed_with_revoked_pair(
    chat_case: ChatCase,
    boundary_model: BoundaryLLM,
) -> None:
    chat_case.model.body = "SYNTHETIC_PASTE_62"
    first = chat_case.send(
        MessageRequest(
            request_id=uuid4(), expected_thread_revision=0, user_input="PRIOR_PRIVATE_QUESTION"
        )
    )
    assert first.turn.state == "available"
    chat_case.revoke()
    second_document = add_document(chat_case)
    second = chat_case.send(
        MessageRequest(
            request_id=uuid4(),
            expected_thread_revision=1,
            user_input="Я вручную вставил SYNTHETIC_PASTE_62",
        )
    )
    assert second.turn.state == "available"
    request = boundary_model.requests[-1]
    assert request.provenance_valid
    assert {ref.document_id for ref in request.source_refs} == {second_document.id}
    assert "PRIOR_PRIVATE_QUESTION" not in request.system_supplied_context
    result = observe_boundary(request, output=second.turn.answer, marker="SYNTHETIC_PASTE_62")
    assert result.user_marker_present
    assert result.user_origin_marker
    assert result.output_marker_present
    assert not result.system_marker_present
    assert not result.system_violation
    assert chat_case.read().turns[0].state == "unavailable"


def test_user_origin_survives_real_second_hop_history(
    chat_case: ChatCase,
    boundary_model: BoundaryLLM,
) -> None:
    chat_case.model.body = "SYNTHETIC_PASTE_62"
    first = chat_case.send(
        MessageRequest(
            request_id=uuid4(),
            expected_thread_revision=0,
            user_input="Я вручную вставил SYNTHETIC_PASTE_62",
        )
    )
    assert first.turn.state == "available"
    boundary_model.prior_origins = boundary_model.requests[-1].user_origin_markers
    second = chat_case.send(
        MessageRequest(
            request_id=uuid4(), expected_thread_revision=1, user_input="Повтори предыдущий ответ"
        )
    )
    assert second.turn.state == "available"
    request = boundary_model.requests[-1]
    assert request.provenance_valid
    assert {ref.document_id for ref in request.source_refs} == {chat_case.document.id}
    result = observe_boundary(request, output=second.turn.answer, marker="SYNTHETIC_PASTE_62")
    assert not result.user_marker_present
    assert result.user_origin_marker
    assert result.system_marker_present
    assert result.output_marker_present
    assert not result.system_violation
