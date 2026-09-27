from uuid import uuid4

import pytest
from pydantic import ValidationError

from rag_access_guard_api.schemas.chat import UnavailableTurn


def test_unavailable_projection_contains_only_safe_fields() -> None:
    turn_id, request_id = uuid4(), uuid4()
    turn = UnavailableTurn(id=turn_id, request_id=request_id, user_input="USER_INPUT")
    assert turn.model_dump(mode="json") == {
        "id": str(turn_id),
        "request_id": str(request_id),
        "user_input": "USER_INPUT",
        "state": "unavailable",
        "answer": None,
        "sources": [],
        "message": "Ответ недоступен: права на один из источников изменились.",
    }


@pytest.mark.parametrize("field", ["raw_answer", "metadata", "source_refs", "debug"])
def test_unavailable_projection_rejects_hidden_protected_fields(field: str) -> None:
    with pytest.raises(ValidationError):
        _ = UnavailableTurn.model_validate(
            {"id": uuid4(), "request_id": uuid4(), "user_input": "USER_INPUT", field: "SECRET"}
        )
