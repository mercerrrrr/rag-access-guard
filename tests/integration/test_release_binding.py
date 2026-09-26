from dataclasses import replace
from typing import override
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from rag_access_guard import Guard, PreparedContext
from rag_access_guard_api.adapters import embeddings, llm
from rag_access_guard_api.adapters.policy import PostgresPolicyReader
from rag_access_guard_api.persistence import User
from rag_access_guard_api.routes import chat as routes
from rag_access_guard_api.schemas.chat import MessageRequest, MessageResponse
from rag_access_guard_api.services import chat
from rag_access_guard_api.services.chat_state import ChatConflict, Reservation
from rag_access_guard_api.services.retrieval import retrieve
from tests.integration.test_chat_history_boundaries import add_document
from tests.support.chat import ChatHttp, seed_thread
from tests.support.chat_generation import ChatCase


@pytest.mark.parametrize(
    "fault",
    ["subset", "order", "tokenizer", "thread", "principal", "session", "request", "revision"],
)
@pytest.mark.usefixtures("chat_http")
def test_completion_rejects_rebound_valid_prepared_context(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    _ = add_document(chat_case)
    observed: list[bool] = []

    async def tampered(
        self: chat.ChatService,
        session_token: str,
        outcome: chat.Generated | chat.Neutral,
    ) -> chat.Generated:
        assert isinstance(outcome, chat.Generated)
        attempt, counter = outcome.attempt, outcome.counter
        if fault == "thread":
            with chat_case.database.begin() as connection:
                other = seed_thread(connection)
                _ = connection.execute(
                    text("""INSERT INTO chat_turns
                    (id,thread_id,request_id,request_sha256,expected_thread_revision,user_input,lease_expires_at)
                    SELECT gen_random_uuid(),:other,request_id,request_sha256,
                    expected_thread_revision,user_input,lease_expires_at FROM chat_turns
                    WHERE thread_id=:source AND state='pending'"""),
                    {"other": other, "source": attempt.thread_id},
                )
            changed = replace(attempt, thread_id=other)
        elif fault in {"principal", "session", "request", "revision"}:
            if fault == "principal":
                with chat_case.database.connect() as connection:
                    other_principal = connection.execute(
                        select(User.id).where(User.login == "other")
                    ).scalar_one()
                changed = replace(attempt, principal_id=other_principal)
            elif fault == "session":
                changed = replace(attempt, session_id=uuid4())
            elif fault == "request":
                changed = replace(attempt, request_id=uuid4())
            else:
                changed = replace(attempt, thread_revision=attempt.thread_revision + 1)
        else:
            if fault == "tokenizer":
                counter = llm.FakeTokenCounter(identity="other-tokenizer")
            vector = await embeddings.get_embedding_adapter().embed_query("QUESTION")
            async with self.policy.protected_read(session_token) as uow:
                chunks = await retrieve(uow, vector)
                assert len(chunks) == 2
                selected = chunks[:1] if fault == "subset" else tuple(reversed(chunks))
                prepared = await Guard(counter).prepare_context(
                    attempt.principal_id, selected, (), PostgresPolicyReader(uow)
                )
                assert isinstance(prepared, PreparedContext)
            assert prepared != attempt.prepared
            changed = replace(attempt, prepared=prepared)
        observed.append(True)
        return replace(outcome, attempt=changed, counter=counter)

    class RebindingService(chat.ChatService):
        @override
        async def _complete(
            self,
            session_token: str,
            outcome: chat.Generated | chat.Neutral,
            *,
            expected_attempt: Reservation,
        ) -> MessageResponse | ChatConflict:
            return await super()._complete(
                session_token,
                await tampered(self, session_token, outcome),
                expected_attempt=expected_attempt,
            )

    monkeypatch.setattr(routes, "ChatService", RebindingService)
    response = chat_case.client.post(
        f"/api/chat/threads/{chat_case.thread}/messages",
        json=MessageRequest(
            request_id=uuid4(), expected_thread_revision=0, user_input="QUESTION"
        ).model_dump(mode="json"),
        headers=ChatHttp.csrf(chat_case.client),
    )
    assert observed == [True]
    assert response.status_code == 403
    assert "SYNTHETIC_ANSWER" not in response.text
    with chat_case.database.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM chat_turns WHERE answer IS NOT NULL")
            ).scalar_one()
            == 0
        )
        assert connection.execute(text("SELECT count(*) FROM turn_sources")).scalar_one() == 0
        assert (
            connection.execute(
                text("SELECT count(*) FROM audit_events WHERE stage='release'")
            ).scalar_one()
            == 0
        )
