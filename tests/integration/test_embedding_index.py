import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text

from rag_access_guard_api.adapters import embeddings
from rag_access_guard_api.persistence import PolicyState
from rag_access_guard_api.schemas.documents import DocumentSummary


class PartialEmbedder:
    model_id: str = "intfloat/multilingual-e5-small"
    revision: str = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
    dimension: int = 384

    async def embed_passages(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return ((1.0, *(0.0 for _ in range(383))),) * (len(texts) - 1)

    async def embed_query(self, _text: str) -> tuple[float, ...]:
        return (1.0, *(0.0 for _ in range(383)))


def test_partial_embeddings_never_activate_version(
    admin_client: TestClient,
    auth_database: Engine,
    registered_document: DocumentSummary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(embeddings, "get_embedding_adapter", PartialEmbedder)
    with auth_database.connect() as connection:
        revision = connection.execute(select(PolicyState.revision)).scalar_one()
    response = admin_client.post(
        f"/api/admin/documents/{registered_document.id}/versions",
        files={"file": ("long.txt", b"test " * 1000, "text/plain")},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "Indexing unavailable"}
    with auth_database.connect() as connection:
        assert (
            connection.execute(text("SELECT revision FROM policy_state")).scalar_one() == revision
        )
        assert connection.execute(text("SELECT active_version_id FROM documents")).scalar_one() == (
            registered_document.active_version_id
        )
        assert (
            connection.execute(
                text("SELECT status FROM document_versions WHERE id <> :active"),
                {"active": registered_document.active_version_id},
            ).scalar_one()
            == "failed"
        )
