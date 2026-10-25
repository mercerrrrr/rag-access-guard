from pathlib import Path
from typing import Literal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from rag_access_guard_api.adapters import embeddings
from rag_access_guard_api.adapters.embeddings import (
    E5EmbeddingAdapter,
    IsolatedE5EmbeddingAdapter,
    get_embedding_adapter,
)
from rag_access_guard_api.schemas.access import GrantView
from rag_access_guard_api.schemas.auth import CsrfResponse
from rag_access_guard_api.schemas.documents import DocumentSummary
from rag_access_guard_api.schemas.search import SearchResponse
from rag_access_guard_api.services.passwords import hash_password
from tests.integration.search_fixtures import configure_search

pytestmark = pytest.mark.parametrize(
    "query_cache_profile", ["qwen35-extractive-cpu-demo-v1", "qwen25-extractive-small-demo-v2"]
)


@pytest.fixture
def cached_search(
    registered_document: DocumentSummary,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    query_cache_profile: str,
) -> list[str]:
    assert registered_document.active_version_id is not None
    configure_search(tmp_path / "calibration.json", monkeypatch)
    calls: list[str] = []

    def encode(
        _self: E5EmbeddingAdapter, texts: tuple[str, ...], kind: Literal["query", "passage"]
    ) -> tuple[tuple[float, ...], ...]:
        assert kind == "query"
        calls.extend(texts)
        return tuple((1.0, *(0.0 for _ in range(383))) for _ in texts)

    monkeypatch.setenv("RAG_ACCESS_GUARD_MODEL_PROFILE", query_cache_profile)
    monkeypatch.setattr(E5EmbeddingAdapter, "_encode", encode)
    monkeypatch.setattr(IsolatedE5EmbeddingAdapter, "_encode", encode)
    monkeypatch.setattr(embeddings, "get_embedding_adapter", get_embedding_adapter)
    return calls


def search(client: TestClient) -> SearchResponse:
    response = client.post(
        "/api/search",
        json={"query": "question"},
        headers={"Origin": "https://rag.test", "X-CSRF-Token": client.cookies["__Host-rag_csrf"]},
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    return SearchResponse.model_validate_json(response.content)


def test_cached_query_rechecks_independent_role_and_direct_grants(
    admin_client: TestClient,
    self_grant: GrantView,
    role_grant: GrantView,
    cached_search: list[str],
) -> None:
    # Given both role and direct access, the first query computes one vector.
    assert len(search(admin_client).items) == 1
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
    }
    # When role membership is removed, then the independent direct grant still permits a cache hit.
    assert (
        admin_client.delete(
            f"/api/admin/roles/{role_grant.role_id}/members/{self_grant.user_id}",
            headers=headers,
        ).status_code
        == 204
    )
    assert len(search(admin_client).items) == 1
    # When the remaining direct grant is revoked, then cached computation grants no access.
    assert (
        admin_client.delete(
            f"/api/admin/documents/{self_grant.document_id}/grants/{self_grant.id}",
            headers=headers,
        ).status_code
        == 204
    )
    assert search(admin_client).items == ()
    assert cached_search == ["question"]


def test_cached_query_does_not_share_another_principals_authorization(
    admin_client: TestClient,
    auth_database: Engine,
    self_grant: GrantView,
    cached_search: list[str],
) -> None:
    # Given a permitted principal who has warmed the application's query vector.
    assert self_grant.user_id is not None
    assert len(search(admin_client).items) == 1
    with auth_database.begin() as connection:
        _ = connection.execute(
            text("""INSERT INTO users (id, login, display_name, password_hash)
                    VALUES (:id, 'other', 'Other', :hash)"""),
            {"id": uuid4(), "hash": hash_password("Synthetic-Other-123")},
        )
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
    }
    assert admin_client.post("/api/auth/logout", headers=headers).status_code == 204
    csrf = CsrfResponse.model_validate_json(admin_client.get("/api/auth/csrf").content).csrf_token
    assert (
        admin_client.post(
            "/api/auth/login",
            json={"login": "other", "password": "Synthetic-Other-123"},
            headers={"Origin": "https://rag.test", "X-CSRF-Token": csrf},
        ).status_code
        == 200
    )
    # When another principal hits the same vector cache, then fresh SQL returns no sources.
    assert search(admin_client).items == ()
    assert cached_search == ["question"]
