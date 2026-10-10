from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import anyio
import pytest
from pydantic import HttpUrl
from sqlalchemy import Connection, select, text
from sqlalchemy.exc import IntegrityError

from rag_access_guard import SourceRef
from rag_access_guard_api.adapters import llm
from rag_access_guard_api.adapters.model_tokens import ModelTokenCounter
from rag_access_guard_api.config import Settings
from rag_access_guard_api.database import create_database_engine
from rag_access_guard_api.persistence import Document, DocumentChunk
from rag_access_guard_api.schemas.chat import MessageRequest
from rag_access_guard_api.schemas.documents import DocumentVersionSummary
from rag_access_guard_api.schemas.ingestion import UploadPayload
from rag_access_guard_api.schemas.origin import DocumentOrigin
from rag_access_guard_api.schemas.sources import SourceContent
from rag_access_guard_api.server import create_event_loop
from rag_access_guard_api.services import ingestion
from rag_access_guard_api.services.indexing import index_document_version
from rag_access_guard_api.services.model_profiles import InstructManifest
from rag_access_guard_api.services.sources import build_source_url
from tests.integration.embedding_fixtures import DeterministicEmbedder
from tests.support.chat import ChatHttp
from tests.support.chat_generation import ChatCase


def synthetic_origin() -> DocumentOrigin:
    return DocumentOrigin(
        kind="synthetic_demo",
        publisher="Учебный демонстрационный корпус",
        source_url=None,
        retrieved_at=None,
        published_on=None,
        source_sha256="0" * 64,
        transformation_revision="authored-v1",
    )


def upload_origin(
    case: ChatCase, monkeypatch: pytest.MonkeyPatch, origin: DocumentOrigin
) -> SourceRef:
    prepare = ingestion.prepare_upload

    def annotated(upload: UploadPayload) -> ingestion.PreparedUpload:
        return replace(prepare(upload), origin=origin)

    with monkeypatch.context() as patch:
        patch.setattr(ingestion, "prepare_upload", annotated)
        response = case.client.post(
            f"/api/admin/documents/{case.document.id}/versions",
            files={"file": ("example.txt", b"PROTECTED_SYNTHETIC", "text/plain")},
            headers=ChatHttp.csrf(case.client),
        )
    assert response.status_code == 201, response.text
    with case.database.connect() as connection:
        row = (
            connection.execute(
                select(DocumentChunk.document_version_id, DocumentChunk.id)
                .join(Document, Document.active_version_id == DocumentChunk.document_version_id)
                .where(Document.id == case.document.id)
            )
            .tuples()
            .one()
        )
    return SourceRef(document_id=case.document.id, document_version_id=row[0], chunk_id=row[1])


def request(revision: int = 0) -> MessageRequest:
    return MessageRequest(
        request_id=uuid4(), expected_thread_revision=revision, user_input="Question"
    )


def test_origin_table_is_part_of_fresh_schema(schema_connection: Connection) -> None:
    # Given a migrated disposable database / When / Then
    assert (
        schema_connection.execute(text("SELECT to_regclass('document_origins')")).scalar_one()
        is not None
    )


def test_authorized_source_and_context_keep_origin(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    origin = synthetic_origin()
    ref = upload_origin(chat_case, monkeypatch, origin)
    counter = ModelTokenCounter(InstructManifest())
    monkeypatch.setattr(llm, "get_token_counter", lambda: counter)
    # When
    answer = chat_case.send(request())
    source = chat_case.client.get(build_source_url(ref))
    # Then
    assert answer.turn.state == "available"
    assert answer.turn.sources[0].model_dump(mode="json").get("origin") == origin.model_dump(
        mode="json"
    )
    assert SourceContent.model_validate_json(source.content).origin == origin
    context = chat_case.model.inputs[0][1]
    assert "origin_annotations_v1" in context
    assert "Учебный пример; вымышленный материал, не официальный документ." in context
    assert str(ref.chunk_id) in context
    assert counter.count(context) <= 5000
    assert (
        counter.count_request(user_input="Question", system_supplied_context=context) + 512 <= 8192
    )


@pytest.mark.parametrize(
    "operation", ["UPDATE document_origins SET origin=origin", "DELETE FROM document_origins"]
)
def test_direct_origin_mutation_is_rejected(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    # Given
    _ = upload_origin(chat_case, monkeypatch, synthetic_origin())
    # When / Then
    with chat_case.database.begin() as connection, pytest.raises(IntegrityError, match="immutable"):
        _ = connection.execute(text(operation))


def test_revoke_hides_origin_and_stored_sources(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    ref = upload_origin(chat_case, monkeypatch, synthetic_origin())
    answer = chat_case.send(request())
    assert answer.turn.state == "available"
    # When
    chat_case.revoke()
    # Then
    response = chat_case.client.get(build_source_url(ref))
    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}
    assert response.headers["cache-control"] == "private, no-store"
    turn = chat_case.read().turns[0]
    assert turn.state == "unavailable"
    assert turn.sources == ()
    assert "Учебный" not in response.text
    assert "Synthetic" not in turn.model_dump_json()


def test_version_switch_closes_old_origin_url(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    ref = upload_origin(chat_case, monkeypatch, synthetic_origin())
    # When
    current = upload_origin(
        chat_case,
        monkeypatch,
        synthetic_origin().model_copy(update={"transformation_revision": "authored-v2"}),
    )
    # Then
    assert current.document_version_id != ref.document_version_id
    assert chat_case.client.get(build_source_url(ref)).status_code == 404
    assert chat_case.client.get(build_source_url(current)).status_code == 200


def test_old_null_origin_keeps_source_rights(chat_case: ChatCase) -> None:
    # Given / When
    answer = chat_case.send(request())
    # Then
    assert answer.turn.state == "available"
    assert answer.turn.sources[0].model_dump().get("origin") is None
    assert "origin_annotations_v1" not in chat_case.model.inputs[0][1]


def test_official_origin_projects_only_after_grant(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    origin = DocumentOrigin(
        kind="official_public",
        publisher="Example public publisher",
        source_url=HttpUrl("https://example.org/document"),
        retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
        published_on=None,
        source_sha256="1" * 64,
        transformation_revision="snapshot-v1",
    )
    ref = upload_origin(chat_case, monkeypatch, origin)
    # When
    answer = chat_case.send(request())
    # Then
    assert answer.turn.state == "available"
    assert answer.turn.sources[0].model_dump(mode="json").get("origin") == origin.model_dump(
        mode="json"
    )
    assert (
        SourceContent.model_validate_json(
            chat_case.client.get(build_source_url(ref)).content
        ).origin
        == origin
    )


def test_reindex_preserves_origin_on_new_version(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    origin = synthetic_origin()
    old = upload_origin(chat_case, monkeypatch, origin)

    async def run() -> DocumentVersionSummary:
        engine = create_database_engine(Settings())
        try:
            return await index_document_version(
                engine,
                chat_case.client.cookies["__Host-rag_session"],
                old.document_id,
                old.document_version_id,
                embedder=DeterministicEmbedder(revision="a" * 40),
                csrf_token=chat_case.client.cookies["__Host-rag_csrf"],
            )
        finally:
            await engine.dispose()

    # When
    current = anyio.run(run, backend_options={"loop_factory": create_event_loop})
    # Then
    assert current.id != old.document_version_id
    assert chat_case.client.get(build_source_url(old)).status_code == 404
    with chat_case.database.connect() as connection:
        chunk_id = connection.execute(
            select(DocumentChunk.id).where(DocumentChunk.document_version_id == current.id)
        ).scalar_one()
        records = connection.execute(
            text(
                "SELECT origin,origin_sha256 FROM document_origins WHERE version_id IN (:old,:new)"
            ),
            {"old": old.document_version_id, "new": current.id},
        ).all()
    assert len(records) == 2
    assert records[0] == records[1]
    new_ref = SourceRef(
        document_id=old.document_id, document_version_id=current.id, chunk_id=chunk_id
    )
    response = chat_case.client.get(build_source_url(new_ref))
    assert response.status_code == 200
    assert SourceContent.model_validate_json(response.content).origin == origin
