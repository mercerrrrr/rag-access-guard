from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from rag_access_guard_api.schemas.chat import TurnView


@pytest.mark.parametrize(
    "fields",
    [
        {"state": "available", "answer": "Unverified", "sources": []},
        {"state": "pending", "answer": "Premature output"},
        {"state": "neutral", "message": "Raw exception"},
        {"state": "unavailable", "answer": "Revoked output"},
        {"state": "unavailable", "sources": [{"title": "Closed document"}]},
        {"state": "unknown"},
    ],
)
def test_invalid_turn_projection_cannot_be_serialized(fields: dict[str, object]) -> None:
    payload = {"id": uuid4(), "request_id": uuid4(), "user_input": "Question", **fields}
    with pytest.raises(ValidationError):
        _ = TypeAdapter[TurnView](TurnView).validate_python(payload)


def test_unavailable_projection_keeps_user_input_without_protected_content() -> None:
    turn = TypeAdapter[TurnView](TurnView).validate_python(
        {"id": uuid4(), "request_id": uuid4(), "user_input": "Pasted text", "state": "unavailable"}
    )
    assert turn.user_input == "Pasted text"
    assert turn.answer is None
    assert turn.sources == ()
    assert turn.message == "Ответ недоступен: права на один из источников изменились."
