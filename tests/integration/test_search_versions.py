from pathlib import Path
from typing import override
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from rag_access_guard_api.adapters import embeddings
from rag_access_guard_api.schemas.access import GrantView
from rag_access_guard_api.schemas.documents import DocumentSummary
from rag_access_guard_api.schemas.search import SearchResponse
from tests.integration.embedding_fixtures import DeterministicEmbedder
from tests.integration.search_fixtures import configure_search


@pytest.fixture
def search_client(
    admin_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    configure_search(tmp_path / "config.json", monkeypatch)
    return admin_client


def test_old_versions_are_absent(
    search_client: TestClient, self_grant: GrantView, registered_document: DocumentSummary
) -> None:
    assert self_grant.user_id is not None
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": search_client.cookies["__Host-rag_csrf"],
    }
    uploaded = search_client.post(
        f"/api/admin/documents/{registered_document.id}/versions",
        files={"file": ("new.txt", b"NEW_CURRENT_VERSION", "text/plain")},
        headers=headers,
    )
    assert uploaded.status_code == 201
    response = search_client.post("/api/search", json={"query": "question"}, headers=headers)
    assert response.status_code == 200
    items = SearchResponse.model_validate_json(response.content).items
    assert len(items) == 1
    assert items[0].text == "NEW_CURRENT_VERSION"
    assert items[0].source_ref.document_version_id != registered_document.active_version_id


def test_wrong_model_version_is_absent(
    search_client: TestClient,
    self_grant: GrantView,
    registered_document: DocumentSummary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert self_grant.user_id is not None
    monkeypatch.setattr(
        embeddings, "get_embedding_adapter", lambda: DeterministicEmbedder(revision="b" * 40)
    )
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": search_client.cookies["__Host-rag_csrf"],
    }
    uploaded = search_client.post(
        f"/api/admin/documents/{registered_document.id}/versions",
        files={"file": ("new.txt", b"OTHER_MODEL", "text/plain")},
        headers=headers,
    )
    assert uploaded.status_code == 201
    monkeypatch.setattr(embeddings, "get_embedding_adapter", DeterministicEmbedder)
    response = search_client.post("/api/search", json={"query": "question"}, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_direct_and_role_union_searches_same_set(
    search_client: TestClient,
    self_grant: GrantView,
    role_grant: GrantView,
    role_id: UUID,
    role_member_id: UUID,
) -> None:
    assert self_grant.document_id == role_grant.document_id
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": search_client.cookies["__Host-rag_csrf"],
    }
    before = search_client.post("/api/search", json={"query": "question"}, headers=headers)
    assert before.status_code == 200
    assert len(SearchResponse.model_validate_json(before.content).items) == 1
    assert (
        search_client.delete(
            f"/api/admin/roles/{role_id}/members/{role_member_id}", headers=headers
        ).status_code
        == 204
    )
    after = search_client.post("/api/search", json={"query": "question"}, headers=headers)
    assert after.status_code == 200
    assert after.json() == before.json()


def test_threshold_excludes_allowed_but_irrelevant_chunks(
    search_client: TestClient, self_grant: GrantView, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert self_grant.user_id is not None

    class OrthogonalQuery(DeterministicEmbedder):
        @override
        async def embed_query(self, text: str) -> tuple[float, ...]:
            assert text
            return (0.0, 1.0, *(0.0 for _ in range(382)))

    monkeypatch.setattr(embeddings, "get_embedding_adapter", OrthogonalQuery)
    response = search_client.post(
        "/api/search",
        json={"query": "unrelated question"},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": search_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 200
    assert response.json() == {"items": []}
