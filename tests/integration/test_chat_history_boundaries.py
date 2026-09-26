from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text

from rag_access_guard import (
    CandidateChunk,
    Guard,
    PolicyReader,
    PreparedContext,
    PrepareDenied,
    PriorTurn,
)
from rag_access_guard_api.persistence import User
from rag_access_guard_api.schemas.chat import MessageRequest, ThreadView
from rag_access_guard_api.schemas.documents import DocumentSummary
from rag_access_guard_api.services import chat
from rag_access_guard_api.services.retrieval import retrieve
from rag_access_guard_api.services.security import ReadUoW
from tests.integration.test_chat_history import ask
from tests.support.chat import ChatHttp
from tests.support.chat_generation import ChatCase


def add_document(case: ChatCase) -> DocumentSummary:
    response = case.client.post(
        "/api/admin/documents",
        data={"title": "Second synthetic"},
        files={"file": ("second.txt", b"SECOND_ALLOWED_MARKER", "text/plain")},
        headers=ChatHttp.csrf(case.client),
    )
    assert response.status_code == 201
    document = DocumentSummary.model_validate_json(response.content)
    with case.database.connect() as connection:
        user_id = connection.execute(select(User.id).where(User.login == "reader")).scalar_one()
    response = case.client.post(
        f"/api/admin/documents/{document.id}/grants",
        json={"user_id": str(user_id)},
        headers=ChatHttp.csrf(case.client),
    )
    assert response.status_code == 201
    return document


def test_transitive_history_closure_survives_retrieval_change_and_revocation(
    chat_case: ChatCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    second = add_document(chat_case)
    selected = [chat_case.document.id]

    async def selected_retrieval(
        uow: ReadUoW,
        vector: tuple[float, ...],
        *,
        limit: int = 5,
    ) -> tuple[CandidateChunk, ...]:
        return tuple(
            c
            for c in await retrieve(uow, vector, limit=limit)
            if c.source_ref.document_id in selected
        )

    monkeypatch.setattr(chat, "retrieve", selected_retrieval)
    ask(chat_case, 1)
    selected[:] = [second.id]
    ask(chat_case, 2)
    ask(chat_case, 3)
    detail = chat_case.read()
    assert {s.document_id for s in detail.turns[2].sources} == {chat_case.document.id, second.id}
    chat_case.revoke()
    ask(chat_case, 4)
    user, context = chat_case.model.inputs[-1]
    assert user == "HISTORY_QUESTION_4"
    assert "SECOND_ALLOWED_MARKER" in context
    assert "PROTECTED_SYNTHETIC" not in context
    for ordinal in range(1, 4):
        assert f"HISTORY_QUESTION_{ordinal}" not in context
        assert f"HISTORY_ANSWER_{ordinal}" not in context
    detail = chat_case.read()
    for turn in detail.turns[:3]:
        assert turn.state == "unavailable"
        assert turn.answer is None
        assert turn.sources == ()
        assert turn.user_input.startswith("HISTORY_QUESTION_")
    assert {s.document_id for s in detail.turns[3].sources} == {second.id}


def test_new_thread_never_receives_another_threads_history(chat_case: ChatCase) -> None:
    ask(chat_case, 1)
    response = chat_case.client.post(
        "/api/chat/threads",
        json={},
        headers=ChatHttp.csrf(chat_case.client),
    )
    assert response.status_code == 201
    other = replace(chat_case, thread=ThreadView.model_validate_json(response.content).id)
    ask(other, 1)
    assert "HISTORY_ANSWER_1" not in other.model.inputs[-1][1]


def test_forged_history_body_with_allowed_sources_never_reaches_model(
    chat_case: ChatCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ask(chat_case, 1)
    original = Guard.prepare_context

    async def forged(
        self: Guard,
        principal_id: UUID,
        candidate_chunks: tuple[CandidateChunk, ...],
        prior_turns: tuple[PriorTurn, ...],
        policy_reader: PolicyReader,
    ) -> PreparedContext | PrepareDenied:
        turns = tuple(replace(t, answer="FORGED_BODY") for t in prior_turns)
        return await original(self, principal_id, candidate_chunks, turns, policy_reader)

    monkeypatch.setattr(Guard, "prepare_context", forged)
    result = chat_case.send(
        MessageRequest(
            request_id=uuid4(),
            expected_thread_revision=1,
            user_input="NEW_QUESTION",
        )
    )
    assert result.turn.state == "neutral"
    assert chat_case.model.call_count == 1
    assert "FORGED_BODY" not in result.model_dump_json()


@pytest.mark.parametrize("change", ["history_body", "thread_revision"])
def test_concurrent_canonical_change_prevents_history_answer_release(
    chat_case: ChatCase,
    change: str,
) -> None:
    ask(chat_case, 1)
    chat_case.model.entered.clear()
    chat_case.model.hold = True
    chat_case.model.body = "UNRELEASED_HISTORY_RESULT"
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(
            chat_case.client.post,
            f"/api/chat/threads/{chat_case.thread}/messages",
            json=MessageRequest(
                request_id=uuid4(), expected_thread_revision=1, user_input="NEW_QUESTION"
            ).model_dump(mode="json"),
            headers=ChatHttp.csrf(chat_case.client),
        )
        try:
            assert chat_case.model.entered.wait(10)
            with chat_case.database.begin() as connection:
                statement = (
                    "UPDATE chat_turns SET answer='CHANGED_BODY' WHERE state='available'"
                    if change == "history_body"
                    else "UPDATE chat_threads SET revision=revision+1"
                )
                _ = connection.execute(text(statement))
        finally:
            chat_case.model.resume.set()
        response = running.result(10)
    assert response.status_code == (200 if change == "history_body" else 409)
    assert "UNRELEASED_HISTORY_RESULT" not in response.text
    with chat_case.database.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM chat_turns WHERE answer='UNRELEASED_HISTORY_RESULT'")
            ).scalar_one()
            == 0
        )
