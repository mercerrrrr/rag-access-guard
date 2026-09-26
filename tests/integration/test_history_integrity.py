import pytest
from sqlalchemy import text

from tests.integration.test_chat_history import ask
from tests.integration.test_chat_history_boundaries import add_document
from tests.support.chat_generation import ChatCase


@pytest.mark.parametrize("lose_source", [False, True])
def test_partial_source_loss_never_launders_revoked_history(
    chat_case: ChatCase,
    *,
    lose_source: bool,
) -> None:
    _ = add_document(chat_case)
    ask(chat_case, 1)
    assert len(chat_case.read().turns[0].sources) == 2
    if lose_source:
        with chat_case.database.begin() as connection:
            removed = connection.execute(
                text("DELETE FROM turn_sources WHERE document_id=:document"),
                {"document": chat_case.document.id},
            )
            assert removed.rowcount == 1
    chat_case.revoke()
    ask(chat_case, 2)
    context = chat_case.model.inputs[-1][1]
    assert "SECOND_ALLOWED_MARKER" in context
    assert "HISTORY_QUESTION_1" not in context
    assert "HISTORY_ANSWER_1" not in context
    first = chat_case.read().turns[0]
    assert first.state == "unavailable"
    assert first.answer is None
    assert first.sources == ()
