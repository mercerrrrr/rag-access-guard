from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import override

import pytest
from anyio import to_thread
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text

from rag_access_guard_api.adapters import embeddings
from rag_access_guard_api.persistence import PolicyState
from rag_access_guard_api.schemas.documents import DocumentSummary, DocumentVersionSummary
from tests.integration.embedding_fixtures import DeterministicEmbedder


@pytest.mark.parametrize(
    "case",
    [
        ("UPDATE sessions SET revoked_at=clock_timestamp()", 401),
        ("UPDATE users SET is_admin=false", 403),
        ("UPDATE documents SET is_active=false", 403),
        ("UPDATE sessions SET csrf_token_digest=decode(repeat('ab',32),'hex')", 403),
    ],
)
def test_authority_revoked_during_embedding_blocks_activation(
    registered_document: DocumentSummary,
    admin_client: TestClient,
    auth_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[str, int],
) -> None:
    change, status = case

    class RevokingEmbedder(DeterministicEmbedder):
        @override
        async def embed_passages(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
            with auth_database.begin() as connection:
                _ = connection.execute(text("SELECT revision FROM policy_state FOR UPDATE NOWAIT"))
                _ = connection.execute(text(change))
            return await super().embed_passages(texts)

    monkeypatch.setattr(embeddings, "get_embedding_adapter", RevokingEmbedder)
    response = admin_client.post(
        f"/api/admin/documents/{registered_document.id}/versions",
        files={"file": ("new.txt", b"new text", "text/plain")},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == status
    with auth_database.connect() as connection:
        assert (
            connection.execute(text("SELECT active_version_id FROM documents")).scalar_one()
            == registered_document.active_version_id
        )
        assert connection.execute(text("SELECT count(*) FROM document_versions")).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM chunk_embeddings")).scalar_one() == 1


def test_older_inference_cannot_overwrite_newer_activation(
    registered_document: DocumentSummary,
    admin_client: TestClient,
    auth_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started, release = Event(), Event()

    class DelayedEmbedder(DeterministicEmbedder):
        @override
        async def embed_passages(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
            if texts == ("older pending",):
                started.set()
                assert await to_thread.run_sync(release.wait, 20)
            return await super().embed_passages(texts)

    monkeypatch.setattr(embeddings, "get_embedding_adapter", DelayedEmbedder)
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
    }
    url = f"/api/admin/documents/{registered_document.id}/versions"
    with auth_database.connect() as connection:
        revision = connection.execute(select(PolicyState.revision)).scalar_one()
    with ThreadPoolExecutor(max_workers=1) as pool:
        older = pool.submit(
            admin_client.post,
            url,
            files={"file": ("old.txt", b"older pending", "text/plain")},
            headers=headers,
        )
        try:
            assert started.wait(20)
            newer = admin_client.post(
                url, files={"file": ("new.txt", b"newer active", "text/plain")}, headers=headers
            )
            assert newer.status_code == 201
            active = DocumentVersionSummary.model_validate_json(newer.content)
        finally:
            release.set()
        conflict = older.result(20)
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": "Version activation conflict"}
    assert conflict.headers["Cache-Control"] == "private, no-store"
    with auth_database.connect() as connection:
        assert (
            connection.execute(text("SELECT active_version_id FROM documents")).scalar_one()
            == active.id
        )
        assert connection.execute(select(PolicyState.revision)).scalar_one() == revision + 1
        assert (
            connection.execute(
                text("SELECT count(*) FROM document_versions WHERE status='ready'")
            ).scalar_one()
            == 3
        )
        assert connection.execute(text("SELECT count(*) FROM chunk_embeddings")).scalar_one() == 3
