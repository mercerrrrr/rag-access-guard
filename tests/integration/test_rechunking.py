from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text

from rag_access_guard_api.config import Settings
from rag_access_guard_api.database import create_database_engine
from rag_access_guard_api.persistence import DocumentChunk, PolicyState
from rag_access_guard_api.schemas.documents import DocumentSummary, DocumentVersionSummary
from rag_access_guard_api.schemas.ingestion import UploadPayload
from rag_access_guard_api.server import create_event_loop
from rag_access_guard_api.services import ingestion
from rag_access_guard_api.services.errors import ForbiddenError
from rag_access_guard_api.services.rechunking import rechunk_version


def test_concurrent_rechunk_creates_separate_complete_versions(
    registered_document: DocumentSummary,
    auth_database: Engine,
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = registered_document.active_version_id
    assert source is not None
    token = admin_client.cookies["__Host-rag_session"]
    csrf = admin_client.cookies["__Host-rag_csrf"]
    barrier = Barrier(2, timeout=15)
    original = ingestion.prepare_upload

    def prepare_together(upload: UploadPayload) -> ingestion.PreparedUpload:
        prepared = original(upload)
        _ = barrier.wait()
        return prepared

    async def reprocess() -> DocumentVersionSummary:
        engine = create_database_engine(Settings())
        try:
            return await rechunk_version(
                engine,
                token,
                registered_document.id,
                source,
                csrf_token=csrf,
            )
        finally:
            await engine.dispose()

    monkeypatch.setattr(ingestion, "prepare_upload", prepare_together)
    with auth_database.connect() as connection:
        original_rows = connection.execute(text("SELECT * FROM document_chunks")).all()
        revision = connection.execute(select(PolicyState.revision)).scalar_one()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            anyio.run, reprocess, backend_options={"loop_factory": create_event_loop}
        )
        second = pool.submit(
            anyio.run, reprocess, backend_options={"loop_factory": create_event_loop}
        )
        versions = (first.result(30), second.result(30))
    assert len({source, versions[0].id, versions[1].id}) == 3
    assert all(version.status == "chunked" for version in versions)
    with auth_database.connect() as connection:
        assert connection.execute(select(PolicyState.revision)).scalar_one() == revision + 2
        assert (
            connection.execute(
                text("SELECT * FROM document_chunks WHERE document_version_id=:id"), {"id": source}
            ).all()
            == original_rows
        )
        for version in versions:
            chunks = connection.execute(
                select(DocumentChunk.id).where(
                    DocumentChunk.document_version_id == version.id,
                    DocumentChunk.document_id == registered_document.id,
                )
            ).all()
            assert len(chunks) == 1
        assert connection.execute(text("SELECT active_version_id FROM documents")).scalar_one() in {
            version.id for version in versions
        }


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE users SET is_admin=false",
        "UPDATE sessions SET csrf_token_digest=decode(repeat('ab',32),'hex')",
    ],
)
def test_rechunk_rechecks_authority_after_preparation(
    registered_document: DocumentSummary,
    auth_database: Engine,
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    source = registered_document.active_version_id
    assert source is not None
    original = ingestion.prepare_upload
    token = admin_client.cookies["__Host-rag_session"]
    csrf = admin_client.cookies["__Host-rag_csrf"]

    def prepare_then_revoke(upload: UploadPayload) -> ingestion.PreparedUpload:
        prepared = original(upload)
        with auth_database.begin() as connection:
            _ = connection.execute(text("SELECT revision FROM policy_state FOR UPDATE NOWAIT"))
            _ = connection.execute(text(change))
        return prepared

    async def reprocess(source: UUID) -> None:
        engine = create_database_engine(Settings())
        try:
            with pytest.raises(ForbiddenError):
                _ = await rechunk_version(
                    engine,
                    token,
                    registered_document.id,
                    source,
                    csrf_token=csrf,
                )
        finally:
            await engine.dispose()

    monkeypatch.setattr(ingestion, "prepare_upload", prepare_then_revoke)
    anyio.run(reprocess, source, backend_options={"loop_factory": create_event_loop})
    with auth_database.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM document_versions")).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM document_chunks")).scalar_one() == 1
