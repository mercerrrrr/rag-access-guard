from dataclasses import replace
from uuid import uuid4

import anyio

from rag_access_guard_api.config import Settings
from rag_access_guard_api.database import create_database_engine
from rag_access_guard_api.schemas.documents import DocumentVersionSummary
from rag_access_guard_api.server import create_event_loop
from rag_access_guard_api.services.indexing import index_document_version
from rag_access_guard_api.services.sources import build_source_url
from tests.helpers.docx_factory import DOCX_MIME, paragraph_table_docx
from tests.integration.embedding_fixtures import DeterministicEmbedder
from tests.integration.test_chat_history import ask
from tests.integration.test_source_boundaries import assert_hidden
from tests.support.chat import ChatHttp
from tests.support.chat_generation import ChatCase
from tests.support.downloads import source_ref


def upload_docx(case: ChatCase) -> tuple[DocumentVersionSummary, bytes]:
    raw = paragraph_table_docx("DOCX_PROTECTED_SYNTHETIC", (("A", "B"),), "End")
    response = case.client.post(
        f"/api/admin/documents/{case.document.id}/versions",
        files={"file": ('unsafe"title.docx', raw, DOCX_MIME)},
        headers=ChatHttp.csrf(case.client),
    )
    assert response.status_code == 201, response.text
    return DocumentVersionSummary.model_validate_json(response.content), raw


def test_docx_grant_source_original_and_revocation_share_one_witness(chat_case: ChatCase) -> None:
    case = chat_case
    _, raw = upload_docx(case)
    ask(case, 1)
    ref = source_ref(case, case.document.id)
    content_url = build_source_url(ref)
    original_url = content_url.replace("/content?", "/original?")
    content = case.client.get(content_url)
    assert content.status_code == 200
    assert content.json()["text"] == "DOCX_PROTECTED_SYNTHETIC\n\nA\tB\n\nEnd"
    original = case.client.get(original_url)
    assert original.status_code == 200
    assert original.content == raw
    assert original.headers["content-type"] == DOCX_MIME
    assert (
        original.headers["content-disposition"] == f'attachment; filename="{case.document.id}.docx"'
    )
    for response in (content, original):
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["vary"] == "Cookie"
        assert response.headers["x-content-type-options"] == "nosniff"
    for mixed in (
        replace(ref, document_id=uuid4()),
        replace(ref, document_version_id=uuid4()),
        replace(ref, chunk_id=uuid4()),
    ):
        url = build_source_url(mixed)
        assert_hidden(case.client.get(url))
        assert_hidden(case.client.get(url.replace("/content?", "/original?")))
    case.revoke()
    assert_hidden(case.client.get(content_url))
    assert_hidden(case.client.get(original_url))
    detail = case.read()
    assert detail.turns[0].state == "unavailable"
    assert detail.turns[0].answer is None
    assert detail.turns[0].sources == ()


def test_docx_reindex_invalidates_old_source_and_history(chat_case: ChatCase) -> None:
    source, raw = upload_docx(chat_case)
    ask(chat_case, 1)
    ref = source_ref(chat_case, chat_case.document.id)
    url = build_source_url(ref)
    assert chat_case.client.get(url).status_code == 200

    async def reindex() -> DocumentVersionSummary:
        engine = create_database_engine(Settings())
        try:
            return await index_document_version(
                engine,
                chat_case.client.cookies["__Host-rag_session"],
                chat_case.document.id,
                source.id,
                embedder=DeterministicEmbedder(revision="a" * 40),
                csrf_token=chat_case.client.cookies["__Host-rag_csrf"],
            )
        finally:
            await engine.dispose()

    current = anyio.run(reindex, backend_options={"loop_factory": create_event_loop})
    assert current.id != source.id
    assert current.status == "ready"
    assert_hidden(chat_case.client.get(url))
    assert_hidden(chat_case.client.get(url.replace("/content?", "/original?")))
    detail = chat_case.read()
    assert detail.turns[0].state == "unavailable"
    assert detail.turns[0].answer is None
    assert detail.turns[0].sources == ()
    new_url = build_source_url(source_ref(chat_case, chat_case.document.id))
    assert chat_case.client.get(new_url.replace("/content?", "/original?")).content == raw
