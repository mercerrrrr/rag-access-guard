from uuid import uuid4

from rag_access_guard_api.schemas.chat import MessageRequest
from tests.support.chat_generation import ChatCase


def test_source_url_is_denied_after_grant_revocation(chat_case: ChatCase) -> None:
    result = chat_case.send(
        MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="Question")
    )
    assert result.turn.state == "available"
    source = result.turn.sources[0]
    response = chat_case.client.get(source.url)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "document_id": str(source.document_id),
        "document_version_id": str(source.document_version_id),
        "chunk_id": str(source.chunk_id),
        "title": "Synthetic",
        "text": "PROTECTED_SYNTHETIC",
    }
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["vary"] == "Cookie"
    chat_case.revoke()
    denied = chat_case.client.get(source.url)
    assert denied.status_code == 404
    assert denied.json() == {"detail": "Not found"}
    assert denied.headers["cache-control"] == "private, no-store"
