from uuid import uuid4

from sqlalchemy import select

from rag_access_guard import SourceRef
from rag_access_guard_api.persistence import DocumentChunk
from rag_access_guard_api.schemas.chat import MessageRequest
from rag_access_guard_api.schemas.documents import DocumentSummary
from rag_access_guard_api.services.sources import build_source_url
from tests.support.chat import ChatHttp
from tests.support.chat_generation import ChatCase


def test_existing_mixed_tuples_and_ungranted_admin_source_are_hidden(chat_case: ChatCase) -> None:
    headers = ChatHttp.csrf(chat_case.client)
    created = chat_case.client.post(
        "/api/admin/documents",
        data={"title": "HIDDEN_TITLE"},
        files={"file": ("hidden.txt", b"HIDDEN_SOURCE", "text/plain")},
        headers=headers,
    )
    assert created.status_code == 201
    hidden = DocumentSummary.model_validate_json(created.content)
    with chat_case.database.connect() as connection:
        row = (
            connection.execute(
                select(
                    DocumentChunk.document_id, DocumentChunk.document_version_id, DocumentChunk.id
                ).where(DocumentChunk.document_id == hidden.id)
            )
            .tuples()
            .one()
        )
    foreign = SourceRef(document_id=row[0], document_version_id=row[1], chunk_id=row[2])
    chat_case.model.body = f"<a href='https://external.invalid/secret'>{foreign.chunk_id}</a>"
    result = chat_case.send(
        MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="Question")
    )
    assert result.turn.state == "available"
    assert len(result.turn.sources) == 1
    source = result.turn.sources[0]
    assert source.document_id == chat_case.document.id
    assert "HIDDEN_TITLE" not in result.model_dump_json()
    mixed = SourceRef(
        document_id=source.document_id,
        document_version_id=source.document_version_id,
        chunk_id=foreign.chunk_id,
    )
    for ref in (foreign, mixed):
        denied = chat_case.client.get(build_source_url(ref))
        assert denied.status_code == 404
        assert denied.json() == {"detail": "Not found"}
    version = chat_case.client.post(
        f"/api/admin/documents/{source.document_id}/versions",
        files={"file": ("new.txt", b"NEW_CANONICAL", "text/plain")},
        headers=headers,
    )
    assert version.status_code == 201
    with chat_case.database.connect() as connection:
        new_version = connection.execute(
            select(DocumentChunk.document_version_id).where(
                DocumentChunk.document_id == source.document_id,
                DocumentChunk.document_version_id != source.document_version_id,
            )
        ).scalar_one()
    old_chunk_new_version = SourceRef(
        document_id=source.document_id, document_version_id=new_version, chunk_id=source.chunk_id
    )
    denied = chat_case.client.get(build_source_url(old_chunk_new_version))
    assert denied.status_code == 404
    assert denied.json() == {"detail": "Not found"}
