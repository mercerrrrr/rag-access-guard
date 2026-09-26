from uuid import uuid4

from sqlalchemy import select

from rag_access_guard_api.persistence import TurnSource
from rag_access_guard_api.schemas.chat import MessageRequest
from tests.support.chat import ChatHttp
from tests.support.chat_generation import ChatCase


def test_release_persists_distinct_first_use_order_without_model_citations(
    chat_case: ChatCase,
) -> None:
    response = chat_case.client.post(
        f"/api/admin/documents/{chat_case.document.id}/versions",
        files={"file": ("long.txt", ("Synthetic paragraph. " * 600).encode(), "text/plain")},
        headers=ChatHttp.csrf(chat_case.client),
    )
    assert response.status_code == 201
    chat_case.model.body = (
        "No citations. <a href='https://external.invalid/secret'>Link</a> " + str(uuid4())
    )
    result = chat_case.send(
        MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="Question")
    )
    assert result.turn.state == "available"
    assert len(result.turn.sources) > 1
    with chat_case.database.connect() as connection:
        rows = (
            connection.execute(
                select(TurnSource.chunk_id, TurnSource.position).order_by(TurnSource.position)
            )
            .tuples()
            .all()
        )
    assert [position for _, position in rows] == list(range(len(rows)))
    assert [ref.chunk_id for ref in result.turn.sources] == [chunk for chunk, _ in rows]
    assert len({ref.chunk_id for ref in result.turn.sources}) == len(rows)
    reread = chat_case.read().turns[0]
    assert reread.sources == result.turn.sources
    offsets = [chat_case.model.inputs[0][1].index(str(ref.chunk_id)) for ref in result.turn.sources]
    assert offsets == sorted(offsets)
    for source in result.turn.sources:
        assert source.url.startswith("/api/documents/")
        assert str(source.chunk_id) in chat_case.model.inputs[0][1]
