from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Literal, assert_never, override
from uuid import UUID, uuid4

import pytest
from pydantic import HttpUrl, TypeAdapter

from rag_access_guard import (
    CandidateChunk,
    Guard,
    PolicyReader,
    PreparedContext,
    PrepareDenied,
    PriorTurn,
    TokenCounter,
)
from rag_access_guard_api.adapters import llm
from rag_access_guard_api.adapters.canonical import CanonicalChunk, canonical_query
from rag_access_guard_api.adapters.model_tokens import ModelTokenCounter
from rag_access_guard_api.routes import chat as routes
from rag_access_guard_api.services import chat
from rag_access_guard_api.services.chat_state import GenerationAttempt
from rag_access_guard_api.services.model_profiles import InstructManifest
from rag_access_guard_api.services.origin_context import OFFICIAL_LABEL, SYNTHETIC_LABEL
from rag_access_guard_api.services.retrieval import retrieve
from rag_access_guard_api.services.security import ReadUoW
from tests.integration.test_chat_history_boundaries import add_document
from tests.integration.test_document_origin import request, synthetic_origin, upload_origin
from tests.support.chat_generation import ChatCase


@pytest.mark.parametrize("unknown", [False, True])
def test_denied_or_unknown_candidates_never_project_origin(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch, *, unknown: bool
) -> None:
    allowed = upload_origin(chat_case, monkeypatch, synthetic_origin())
    second = replace(chat_case, document=add_document(chat_case))
    old = upload_origin(
        second,
        monkeypatch,
        synthetic_origin().model_copy(
            update={
                "kind": "official_public",
                "publisher": "Hidden public publisher",
                "source_url": HttpUrl("https://example.org/hidden"),
                "retrieved_at": datetime(2026, 1, 1, tzinfo=UTC),
            }
        ),
    )
    with chat_case.database.connect() as connection:
        candidates = tuple(
            CanonicalChunk.model_validate(row).candidate()
            for row in connection.execute(canonical_query()).mappings()
            if row["chunk_id"] in (allowed.chunk_id, old.chunk_id)
        )
    _ = upload_origin(second, monkeypatch, synthetic_origin())
    if unknown:
        candidates = tuple(
            replace(c, source_ref=replace(c.source_ref, chunk_id=uuid4()))
            if c.source_ref == old
            else c
            for c in candidates
        )

    async def stale_retrieval(
        uow: ReadUoW, vector: tuple[float, ...], *, limit: int = 5
    ) -> tuple[CandidateChunk, ...]:
        del uow, vector, limit
        return candidates

    monkeypatch.setattr(chat, "retrieve", stale_retrieval)
    response = chat_case.send(request())
    assert "Hidden public publisher" not in response.model_dump_json()
    if unknown:
        assert response.turn.state == "neutral"
        assert chat_case.model.call_count == 0
    else:
        assert response.turn.state == "available"
        assert {s.chunk_id for s in response.turn.sources} == {allowed.chunk_id}
        context = chat_case.model.inputs[0][1]
        assert str(old.chunk_id) not in context
        assert OFFICIAL_LABEL not in context


@dataclass(frozen=True, slots=True)
class BoundaryCounter(ModelTokenCounter):
    """Real selected tokenizer with controlled non-additive boundary observations."""

    mode: Literal["second", "request", "context", "zero"] = "request"
    actual_requests: list[str] = field(default_factory=list, compare=False)

    @override
    def count_request(self, *, user_input: str, system_supplied_context: str) -> int:
        actual = super().count_request(
            user_input=user_input, system_supplied_context=system_supplied_context
        )
        if not system_supplied_context:
            return self.manifest.context_window if self.mode == "zero" else actual
        self.actual_requests.append(system_supplied_context)
        match self.mode:
            case "request":
                return self.manifest.context_window - self.manifest.max_output_tokens + 1
            case "second":
                return (
                    self.manifest.context_window - self.manifest.max_output_tokens + 1
                    if len(self.actual_requests) == 1
                    else actual
                )
            case "context" | "zero":
                return actual
            case _:
                assert_never(self.mode)

    @override
    def count(self, text: str) -> int:
        if self.mode == "context" and text.startswith("{") and "origin_annotations_v1" in text:
            return 5001
        return super().count(text)


@pytest.mark.parametrize(
    ("mode", "expected_calls", "available"),
    [("second", 2, True), ("request", 2, False), ("context", 2, False), ("zero", 0, False)],
)
def test_annotation_budget_uses_at_most_two_local_prepare_passes(
    chat_case: ChatCase,
    monkeypatch: pytest.MonkeyPatch,
    mode: Literal["second", "request", "context", "zero"],
    expected_calls: int,
    *,
    available: bool,
) -> None:
    # Given
    _ = upload_origin(chat_case, monkeypatch, synthetic_origin())
    counter = BoundaryCounter(InstructManifest(), mode=mode)
    monkeypatch.setattr(llm, "get_token_counter", lambda: counter)
    original = Guard.prepare_context
    budgets: list[int] = []

    async def observed(
        self: Guard,
        principal_id: UUID,
        candidate_chunks: tuple[CandidateChunk, ...],
        prior_turns: tuple[PriorTurn, ...],
        policy_reader: PolicyReader,
    ) -> PreparedContext | PrepareDenied:
        budgets.append(self.max_context_tokens)
        return await original(self, principal_id, candidate_chunks, prior_turns, policy_reader)

    monkeypatch.setattr(Guard, "prepare_context", observed)
    # When
    response = chat_case.send(request())
    # Then
    assert len(budgets) == expected_calls
    assert all(0 < b < 5000 for b in budgets)
    if available:
        assert response.turn.state == "available"
        assert chat_case.model.call_count == 1
        assert budgets[1] < budgets[0]
    else:
        assert response.turn.state == "neutral"
        assert response.turn.message == "Нет доступных источников для ответа."
        assert chat_case.model.call_count == 0


@pytest.mark.parametrize("fault", ["digest", "context"])
def test_origin_binding_is_checked_before_model_invocation(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    # Given
    _ = upload_origin(chat_case, monkeypatch, synthetic_origin())

    class TamperingService(chat.ChatService):
        @override
        async def _infer(
            self,
            session_token: str,
            attempt: GenerationAttempt,
            user_input: str,
            counter: TokenCounter,
        ) -> chat.Generated | chat.Neutral:
            changed = (
                replace(attempt, origin_binding="0" * 64)
                if fault == "digest"
                else replace(attempt, model_context=attempt.prepared.model_context)
            )
            return await super()._infer(session_token, changed, user_input, counter)

    monkeypatch.setattr(routes, "ChatService", TamperingService)
    # When
    response = chat_case.send(request())
    # Then
    assert response.turn.state == "neutral"
    assert chat_case.model.call_count == 0


def test_history_pruning_removes_its_annotations(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a history-only synthetic ref and a current official ref.
    old = upload_origin(chat_case, monkeypatch, synthetic_origin())
    first = chat_case.send(request())
    assert first.turn.state == "available"
    current = upload_origin(
        chat_case,
        monkeypatch,
        synthetic_origin().model_copy(
            update={
                "kind": "official_public",
                "publisher": "Example public publisher",
                "source_url": HttpUrl("https://example.org/"),
                "retrieved_at": datetime(2026, 1, 1, tzinfo=UTC),
            }
        ),
    )
    # When: old version becomes denied, so its whole historical pair is pruned.
    answer = chat_case.send(request(1))
    # Then
    assert answer.turn.state == "available"
    context = chat_case.model.inputs[1][1]
    assert str(old.chunk_id) not in context
    assert "Учебный пример; вымышленный материал, не официальный документ." not in context
    assert str(current.chunk_id) in context
    assert "origin_annotations_v1" in context


@pytest.mark.parametrize("prune", [False, True])
def test_actual_used_ref_labels_follow_mixed_context_and_budget_pruning(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch, *, prune: bool
) -> None:
    # Given an authorized synthetic history ref and a distinct official current ref.
    old = upload_origin(chat_case, monkeypatch, synthetic_origin())
    counter = ModelTokenCounter(InstructManifest())
    monkeypatch.setattr(llm, "get_token_counter", lambda: counter)
    chat_case.model.body = "old " * 6000 if prune else "old answer"
    assert chat_case.send(request()).turn.state == "available"
    second = add_document(chat_case)
    other_case = replace(chat_case, document=second)
    current = upload_origin(
        other_case,
        monkeypatch,
        synthetic_origin().model_copy(
            update={
                "kind": "official_public",
                "publisher": "Example public publisher",
                "source_url": HttpUrl("https://example.org/"),
                "retrieved_at": datetime(2026, 1, 1, tzinfo=UTC),
            }
        ),
    )

    async def selected_retrieval(
        uow: ReadUoW, vector: tuple[float, ...], *, limit: int = 5
    ) -> tuple[CandidateChunk, ...]:
        return tuple(c for c in await retrieve(uow, vector, limit=limit) if c.source_ref == current)

    monkeypatch.setattr(chat, "retrieve", selected_retrieval)
    chat_case.model.body = "new answer"
    # When the current request selects only the official ref.
    answer = chat_case.send(request(1))
    # Then the full block maps only the refs actually retained by Guard.
    assert answer.turn.state == "available"
    context = chat_case.model.inputs[-1][1]
    encoded = context.split("\norigin_annotations_v1:\n", 1)[1]
    rows = TypeAdapter(list[tuple[str, str, str, str, str]]).validate_json(encoded)
    labels = {row[2]: row[4] for row in rows}
    assert labels[str(current.chunk_id)] == OFFICIAL_LABEL
    if prune:
        assert str(old.chunk_id) not in labels
        assert str(old.chunk_id) not in context
        assert SYNTHETIC_LABEL not in context
        assert chat_case.read().turns[0].state == "available"
    else:
        assert labels[str(old.chunk_id)] == SYNTHETIC_LABEL
    assert {row[2] for row in rows} == {str(s.chunk_id) for s in answer.turn.sources}
    assert counter.count(context) <= 5000
    assert (
        counter.count_request(user_input="Question", system_supplied_context=context) + 512 <= 8192
    )
