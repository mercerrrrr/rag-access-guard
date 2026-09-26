from uuid import uuid4

from rag_access_guard_api.schemas.chat import MessageRequest
from tests.support.chat import ChatHttp
from tests.support.chat_generation import ChatCase


def ask(case: ChatCase, ordinal: int) -> None:
    case.model.body = f"HISTORY_ANSWER_{ordinal}"
    result = case.send(
        MessageRequest(
            request_id=uuid4(),
            expected_thread_revision=ordinal - 1,
            user_input=f"HISTORY_QUESTION_{ordinal}",
        )
    )
    assert result.turn.state == "available"


def test_model_receives_last_four_canonical_pairs(chat_case: ChatCase) -> None:
    for ordinal in range(1, 6):
        ask(chat_case, ordinal)
    ask(chat_case, 6)
    question, context = chat_case.model.inputs[-1]
    assert question == "HISTORY_QUESTION_6"
    assert "HISTORY_QUESTION_1" not in context
    assert "HISTORY_ANSWER_1" not in context
    for ordinal in range(2, 6):
        assert f"HISTORY_QUESTION_{ordinal}" in context
        assert f"HISTORY_ANSWER_{ordinal}" in context
    assert "HISTORY_QUESTION_6" not in context


def test_client_cannot_supply_forged_history_body(chat_case: ChatCase) -> None:
    response = chat_case.client.post(
        f"/api/chat/threads/{chat_case.thread}/messages",
        json={
            "request_id": str(uuid4()),
            "expected_thread_revision": 0,
            "user_input": "CURRENT",
            "history": [{"answer": "FORGED_PRIVATE_BODY", "source_refs": []}],
        },
        headers=ChatHttp.csrf(chat_case.client),
    )
    assert response.status_code == 422
    assert chat_case.model.call_count == 0


def test_neutral_turn_is_not_reused_as_history(chat_case: ChatCase) -> None:
    chat_case.model.fail = True
    first = chat_case.send(
        MessageRequest(
            request_id=uuid4(),
            expected_thread_revision=0,
            user_input="NEUTRAL_QUESTION",
        )
    )
    assert first.turn.state == "neutral"
    chat_case.model.fail = False
    ask(chat_case, 2)
    assert "NEUTRAL_QUESTION" not in chat_case.model.inputs[-1][1]


def test_pasted_user_text_does_not_gain_inferred_document_provenance(chat_case: ChatCase) -> None:
    chat_case.model.body = "PASTED_USER_MARKER"
    first = chat_case.send(
        MessageRequest(
            request_id=uuid4(),
            expected_thread_revision=0,
            user_input="PASTED_USER_MARKER",
        )
    )
    assert first.turn.state == "available"
    assert {s.document_id for s in first.turn.sources} == {chat_case.document.id}
    ask(chat_case, 2)
    assert '"user_input":"PASTED_USER_MARKER"' in chat_case.model.inputs[-1][1]
    assert {s.document_id for s in chat_case.read().turns[1].sources} == {chat_case.document.id}
