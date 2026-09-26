from uuid import uuid4

import pytest
from sqlalchemy import text

from rag_access_guard_api.schemas.chat import MessageRequest
from rag_access_guard_api.schemas.documents import DocumentSummary
from tests.support.chat import ChatHttp
from tests.support.chat_generation import ChatCase


@pytest.mark.parametrize(
    "extra", ["user_id", "principal_id", "roles", "source_refs", "model", "answer"]
)
def test_client_cannot_supply_identity_provenance_or_model(chat_case: ChatCase, extra: str) -> None:
    payload = {
        "request_id": str(uuid4()),
        "expected_thread_revision": 0,
        "user_input": "QUESTION",
        extra: "forged",
    }
    response = chat_case.client.post(
        f"/api/chat/threads/{chat_case.thread}/messages",
        json=payload,
        headers=ChatHttp.csrf(chat_case.client),
    )
    assert response.status_code == 422
    assert chat_case.model.call_count == 0
    assert chat_case.read().turns == ()


@pytest.mark.parametrize(
    "question",
    ["", " ", "\x00", "я" * 8193, "word " * 1025],
    ids=["empty", "blank", "nul", "bytes", "tokens"],
)
def test_invalid_input_does_not_reserve_or_generate(chat_case: ChatCase, question: str) -> None:
    response = chat_case.client.post(
        f"/api/chat/threads/{chat_case.thread}/messages",
        json={"request_id": str(uuid4()), "expected_thread_revision": 0, "user_input": question},
        headers=ChatHttp.csrf(chat_case.client),
    )
    assert response.status_code == 422
    assert chat_case.read().turns == ()
    assert chat_case.model.call_count == 0


def test_foreign_thread_and_missing_thread_are_indistinguishable(
    chat_case: ChatCase, chat_http: ChatHttp
) -> None:
    bodies: list[str] = []
    for identifier in (chat_case.thread, uuid4()):
        response = chat_http.other.post(
            f"/api/chat/threads/{identifier}/messages",
            json={
                "request_id": str(uuid4()),
                "expected_thread_revision": 0,
                "user_input": "QUESTION",
            },
            headers=ChatHttp.csrf(chat_http.other),
        )
        assert response.status_code == 404
        assert response.headers["cache-control"] == "private, no-store"
        bodies.append(response.text)
    assert bodies[0] == bodies[1]
    assert chat_case.model.call_count == 0


@pytest.mark.parametrize("header", ["Origin", "X-CSRF-Token"])
def test_generation_requires_origin_and_csrf(chat_case: ChatCase, header: str) -> None:
    headers = dict(ChatHttp.csrf(chat_case.client))
    del headers[header]
    response = chat_case.client.post(
        f"/api/chat/threads/{chat_case.thread}/messages",
        json={"request_id": str(uuid4()), "expected_thread_revision": 0, "user_input": "QUESTION"},
        headers=headers,
    )
    assert response.status_code == 403
    assert chat_case.model.call_count == 0


def test_restored_grant_restores_answer_without_rewriting_history(chat_case: ChatCase) -> None:
    request = MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="QUESTION")
    first = chat_case.send(request)
    chat_case.revoke()
    assert chat_case.read().turns[0].state == "unavailable"
    restored = chat_case.client.post(
        f"/api/admin/documents/{chat_case.document.id}/grants",
        json={"user_id": str(chat_case.grant.user_id)},
        headers=ChatHttp.csrf(chat_case.client),
    )
    assert restored.status_code == 201
    replay = chat_case.send(request)
    assert replay.turn == first.turn
    assert chat_case.model.call_count == 1


def test_new_active_version_hides_old_answer_and_source_names(chat_case: ChatCase) -> None:
    _ = chat_case.send(
        MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="QUESTION")
    )
    response = chat_case.client.post(
        f"/api/admin/documents/{chat_case.document.id}/versions",
        files={"file": ("new.txt", b"NEW_VERSION", "text/plain")},
        headers=ChatHttp.csrf(chat_case.client),
    )
    assert response.status_code == 201
    detail = chat_case.read()
    assert detail.turns[0].state == "unavailable"
    assert "Synthetic" not in detail.model_dump_json()


def test_pasted_closed_text_remains_user_input_not_system_provenance(chat_case: ChatCase) -> None:
    chat_case.revoke()
    headers = ChatHttp.csrf(chat_case.client)
    created = chat_case.client.post(
        "/api/admin/documents",
        data={"title": "Allowed second"},
        files={"file": ("second.txt", b"SECOND_ALLOWED", "text/plain")},
        headers=headers,
    )
    assert created.status_code == 201
    document = DocumentSummary.model_validate_json(created.content)
    assert (
        chat_case.client.post(
            f"/api/admin/documents/{document.id}/grants",
            json={"user_id": str(chat_case.grant.user_id)},
            headers=headers,
        ).status_code
        == 201
    )
    result = chat_case.send(
        MessageRequest(
            request_id=uuid4(), expected_thread_revision=0, user_input="PROTECTED_SYNTHETIC"
        )
    )
    assert result.turn.state == "available"
    user, system = chat_case.model.inputs[0]
    assert user == "PROTECTED_SYNTHETIC"
    assert "PROTECTED_SYNTHETIC" not in system
    assert "SECOND_ALLOWED" in system
    assert {source.document_id for source in result.turn.sources} == {document.id}
    with chat_case.database.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM turn_sources")).scalar_one() == 1


def test_title_uses_only_first_user_input_and_model_has_no_hidden_history(
    chat_case: ChatCase,
) -> None:
    first = MessageRequest(
        request_id=uuid4(), expected_thread_revision=0, user_input="  FIRST   QUESTION  "
    )
    _ = chat_case.send(first)
    _ = chat_case.send(
        MessageRequest(request_id=uuid4(), expected_thread_revision=1, user_input="SECOND QUESTION")
    )
    assert chat_case.read().title == "FIRST QUESTION"
    user, context = chat_case.model.inputs[1]
    assert user == "SECOND QUESTION"
    assert "FIRST" not in context
    assert "SYNTHETIC_ANSWER" not in context
