from dataclasses import dataclass, replace
from uuid import uuid4

import pytest
from httpx2 import Response
from pydantic import TypeAdapter
from sqlalchemy import text
from tests.integration.test_chat_history_boundaries import add_document
from tests.support.chat import ChatHttp
from tests.support.chat_generation import ChatCase

from rag_access_guard_api.schemas.access import GrantView
from rag_access_guard_api.schemas.chat import MessageRequest, MessageResponse
from rag_access_guard_api.schemas.documents import DocumentSummary


@dataclass(frozen=True, slots=True)
class StoredChatCase:
    case: ChatCase
    independent_document: DocumentSummary
    requests: tuple[MessageRequest, ...]
    answers: tuple[MessageResponse, ...]

    def read(self) -> Response:
        return self.case.client.get(f"/api/chat/threads/{self.case.thread}")

    def restore(self) -> GrantView:
        response = self.case.client.post(
            f"/api/admin/documents/{self.case.document.id}/grants",
            json={"user_id": str(self.case.grant.user_id)},
            headers=ChatHttp.csrf(self.case.client),
        )
        assert response.status_code == 201
        return GrantView.model_validate_json(response.content)

    def persisted(self) -> tuple[str, ...]:
        rows: list[str] = []
        with self.case.database.connect() as connection:
            for statement in (
                "SELECT row_to_json(t)::text FROM chat_turns t ORDER BY id",
                """SELECT row_to_json(s)::text FROM turn_sources s
                ORDER BY turn_id, document_id, document_version_id, chunk_id""",
            ):
                rows.extend(
                    TypeAdapter(tuple[str, ...]).validate_python(
                        connection.execute(text(statement)).scalars().all()
                    )
                )
        return tuple(rows)


@pytest.fixture
def stored_chat_case(chat_case: ChatCase) -> StoredChatCase:
    requests = tuple(
        MessageRequest(
            request_id=uuid4(), expected_thread_revision=index, user_input=f"QUESTION_{name}"
        )
        for index, name in enumerate("ABCD")
    )
    answers: list[MessageResponse] = []
    chat_case.model.body = "ANSWER_A"
    answers.append(chat_case.send(requests[0]))
    independent = add_document(chat_case)
    for index in (1, 2):
        chat_case.model.body = f"ANSWER_{'ABCD'[index]}"
        answers.append(chat_case.send(requests[index]))
    chat_case.revoke()
    chat_case.model.body = "ANSWER_D"
    answers.append(chat_case.send(requests[3]))
    fixture = StoredChatCase(chat_case, independent, requests, tuple(answers))
    restored = fixture.restore()
    fixture = replace(fixture, case=replace(chat_case, grant=restored))
    expected = (
        {chat_case.document.id},
        {chat_case.document.id, independent.id},
        {chat_case.document.id, independent.id},
        {independent.id},
    )
    assert all(answer.turn.state == "available" for answer in answers)
    assert tuple({source.document_id for source in a.turn.sources} for a in answers) == expected
    return fixture
