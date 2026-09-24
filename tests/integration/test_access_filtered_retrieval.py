from dataclasses import dataclass
from math import sqrt
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from rag_access_guard_api.adapters import embeddings
from rag_access_guard_api.persistence import User
from rag_access_guard_api.schemas.documents import DocumentSummary
from rag_access_guard_api.schemas.search import SearchResponse
from tests.integration.search_fixtures import configure_search


@dataclass(frozen=True, slots=True)
class RankedEmbedder:
    model_id: str = "intfloat/multilingual-e5-small"
    revision: str = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
    dimension: int = 384

    async def embed_passages(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(
            (score, sqrt(1 - score * score), *(0.0 for _ in range(382)))
            for value in texts
            for score in (1.0 if "CLOSED_MARKER" in value else 0.9,)
        )

    async def embed_query(self, _text: str) -> tuple[float, ...]:
        return (1.0, *(0.0 for _ in range(383)))


def test_acl_filter_precedes_top_five(
    admin_client: TestClient,
    auth_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configure_search(tmp_path / "calibration.json", monkeypatch)
    monkeypatch.setattr(embeddings, "get_embedding_adapter", RankedEmbedder)
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
    }
    with auth_database.connect() as connection:
        principal = connection.execute(select(User.id).where(User.login == "reader")).scalar_one()
    allowed: set[str] = set()
    for index in range(11):
        content = f"CLOSED_MARKER {index}" if index < 6 else f"ALLOWED_MARKER {index}"
        response = admin_client.post(
            "/api/admin/documents",
            data={"title": f"Synthetic {index}"},
            files={"file": ("source.txt", content.encode(), "text/plain")},
            headers=headers,
        )
        assert response.status_code == 201
        document = DocumentSummary.model_validate_json(response.content)
        if index >= 6:
            grant = admin_client.post(
                f"/api/admin/documents/{document.id}/grants",
                json={"user_id": str(principal)},
                headers=headers,
            )
            assert grant.status_code == 201
            allowed.add(str(document.id))
    response = admin_client.post(
        "/api/search", json={"query": "work requirements"}, headers=headers
    )
    assert response.status_code == 200
    result = SearchResponse.model_validate_json(response.content)
    assert len(result.items) == 5
    assert {str(item.source_ref.document_id) for item in result.items} == allowed
    assert "CLOSED_MARKER" not in response.text
