from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import anyio
import pytest
from anyio import to_thread
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text

from rag_access_guard_api.config import Settings
from rag_access_guard_api.database import create_database_engine
from rag_access_guard_api.persistence import PolicyState
from rag_access_guard_api.schemas.documents import DocumentSummary, DocumentVersionSummary
from rag_access_guard_api.schemas.embedding_vectors import VersionActivationConflictError
from rag_access_guard_api.server import create_event_loop
from rag_access_guard_api.services.indexing import index_document_version
from rag_access_guard_api.services.rechunking import rechunk_version
from rag_access_guard_api.services.security import PolicyUnitOfWork, ReadUoW
from tests.integration.embedding_fixtures import DeterministicEmbedder


@pytest.mark.parametrize("operation", ["reindex", "rechunk"])
def test_activation_after_source_read_cannot_be_overwritten(
    registered_document: DocumentSummary,
    admin_client: TestClient,
    auth_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    source = registered_document.active_version_id
    assert source is not None
    token = admin_client.cookies["__Host-rag_session"]
    csrf = admin_client.cookies["__Host-rag_csrf"]
    original_read = PolicyUnitOfWork.protected_read
    injected = False
    published: list[DocumentVersionSummary] = []
    with auth_database.connect() as connection:
        revision = connection.execute(select(PolicyState.revision)).scalar_one()

    @asynccontextmanager
    async def read_then_activate(
        policy: PolicyUnitOfWork, session_token: str
    ) -> AsyncGenerator[ReadUoW]:
        nonlocal injected
        async with original_read(policy, session_token) as uow:
            yield uow
        if not injected:
            injected = True
            response = await to_thread.run_sync(
                lambda: admin_client.post(
                    f"/api/admin/documents/{registered_document.id}/versions",
                    files={"file": ("new.txt", b"newer synthetic version", "text/plain")},
                    headers={"Origin": "https://rag.test", "X-CSRF-Token": csrf},
                )
            )
            assert response.status_code == 201
            published.append(DocumentVersionSummary.model_validate_json(response.content))

    async def run() -> None:
        engine = create_database_engine(Settings())
        try:
            request = (
                index_document_version(
                    engine,
                    token,
                    registered_document.id,
                    source,
                    embedder=DeterministicEmbedder(),
                    csrf_token=csrf,
                )
                if operation == "reindex"
                else rechunk_version(
                    engine,
                    token,
                    registered_document.id,
                    source,
                    csrf_token=csrf,
                )
            )
            with pytest.raises(VersionActivationConflictError):
                _ = await request
        finally:
            await engine.dispose()

    monkeypatch.setattr(PolicyUnitOfWork, "protected_read", read_then_activate)
    anyio.run(run, backend_options={"loop_factory": create_event_loop})
    assert len(published) == 1
    with auth_database.connect() as connection:
        assert connection.execute(select(PolicyState.revision)).scalar_one() == revision + 1
        assert (
            connection.execute(text("SELECT active_version_id FROM documents")).scalar_one()
            == published[0].id
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM document_versions WHERE status='ready'")
            ).scalar_one()
            == 3
        )
        assert connection.execute(text("SELECT count(*) FROM chunk_embeddings")).scalar_one() == 3
