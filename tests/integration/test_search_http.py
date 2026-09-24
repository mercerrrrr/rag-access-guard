from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from rag_access_guard_api.schemas.access import GrantView
from rag_access_guard_api.schemas.documents import DocumentSummary
from rag_access_guard_api.schemas.search import SearchResponse
from tests.integration.search_fixtures import configure_search


@pytest.fixture
def search_client(
    admin_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    configure_search(tmp_path / "calibration.json", monkeypatch)
    return admin_client


def test_empty_access_returns_empty_items(
    search_client: TestClient, registered_document: DocumentSummary
) -> None:
    response = search_client.post(
        "/api/search",
        json={"query": "PROTECTED_SYNTHETIC"},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": search_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 200
    assert response.json() == {"items": []}
    assert str(registered_document.id) not in response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["vary"] == "Cookie"


@pytest.mark.parametrize("extra", ["user_id", "principal_id", "roles", "source_refs", "text"])
def test_search_rejects_client_roles_and_principal(search_client: TestClient, extra: str) -> None:
    response = search_client.post(
        "/api/search",
        json={"query": "question", extra: str(uuid4())},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": search_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 422
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize(
    "query",
    ["", "  ", "word " * 513, "я" * 16384],
    ids=["empty", "blank", "token-limit", "byte-limit"],
)
def test_query_is_bounded_without_truncation(search_client: TestClient, query: str) -> None:
    response = search_client.post(
        "/api/search",
        json={"query": query},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": search_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 422
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize("limit", [0, 6, -1, True, 1.5])
def test_production_limit_cannot_exceed_five(search_client: TestClient, limit: float) -> None:
    response = search_client.post(
        "/api/search",
        json={"query": "question", "limit": limit},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": search_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("origin", "csrf"), [("https://foreign.test", True), (None, True), ("https://rag.test", False)]
)
def test_search_requires_origin_and_csrf(
    search_client: TestClient, origin: str | None, *, csrf: bool
) -> None:
    headers: dict[str, str] = {}
    if origin is not None:
        headers["Origin"] = origin
    if csrf:
        headers["X-CSRF-Token"] = search_client.cookies["__Host-rag_csrf"]
    response = search_client.post("/api/search", json={"query": "question"}, headers=headers)
    assert response.status_code == 403
    assert response.headers["cache-control"] == "private, no-store"


def test_search_revocation_removes_result(
    search_client: TestClient, self_grant: GrantView, registered_document: DocumentSummary
) -> None:
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": search_client.cookies["__Host-rag_csrf"],
    }
    first = search_client.post("/api/search", json={"query": "question"}, headers=headers)
    assert first.status_code == 200
    assert len(SearchResponse.model_validate_json(first.content).items) == 1
    revoked = search_client.delete(
        f"/api/admin/documents/{registered_document.id}/grants/{self_grant.id}", headers=headers
    )
    assert revoked.status_code == 204
    second = search_client.post("/api/search", json={"query": "question"}, headers=headers)
    assert second.status_code == 200
    assert second.json() == {"items": []}


def test_search_error_does_not_expose_query_or_document_text(
    search_client: TestClient, auth_database: Engine, self_grant: GrantView
) -> None:
    assert self_grant.user_id is not None
    with auth_database.begin() as connection:
        _ = connection.execute(text("DELETE FROM policy_state"))
    response = search_client.post(
        "/api/search",
        json={"query": "PRIVATE_QUERY_MARKER"},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": search_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 503
    assert "PRIVATE_QUERY_MARKER" not in response.text
    assert "PROTECTED_SYNTHETIC" not in response.text
    assert response.headers["cache-control"] == "private, no-store"


def test_missing_calibration_fails_closed(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RAG_ACCESS_GUARD_RETRIEVAL_CONFIG_PATH", raising=False)
    response = admin_client.post(
        "/api/search",
        json={"query": "question"},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "Retrieval not configured"}
