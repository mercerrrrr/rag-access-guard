from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from rag_access_guard_api.adapters import embeddings
from rag_access_guard_api.adapters.tokenizer import MODEL_REVISION
from rag_access_guard_api.config import Settings
from rag_access_guard_api.persistence import User
from rag_access_guard_api.schemas.documents import DocumentSummary
from rag_access_guard_api.schemas.retrieval_config import load_retrieval_config
from rag_access_guard_api.schemas.search import SearchResponse
from tests.calibration_corpus import synthetic_calibration_dataset


def test_real_calibrated_search_and_no_relevant_queries(
    admin_client: TestClient, auth_database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_retrieval_config(Settings().retrieval_config_path)
    config.require_compatible()
    real = embeddings.E5EmbeddingAdapter(Path(f".cache/e5/{MODEL_REVISION}"))
    monkeypatch.setattr(embeddings, "get_embedding_adapter", lambda: real)
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
    }
    with auth_database.connect() as connection:
        principal = connection.execute(select(User.id).where(User.login == "reader")).scalar_one()
    corpus = synthetic_calibration_dataset()
    for chunk in corpus.corpus:
        uploaded = admin_client.post(
            "/api/admin/documents",
            data={"title": "Synthetic calibration"},
            files={"file": ("source.txt", chunk.text.encode(), "text/plain")},
            headers=headers,
        )
        assert uploaded.status_code == 201
        document = DocumentSummary.model_validate_json(uploaded.content)
        grant = admin_client.post(
            f"/api/admin/documents/{document.id}/grants",
            json={"user_id": str(principal)},
            headers=headers,
        )
        assert grant.status_code == 201
    for query in corpus.queries:
        response = admin_client.post("/api/search", json={"query": query.query}, headers=headers)
        assert response.status_code == 200
        result = SearchResponse.model_validate_json(response.content)
        if not query.relevant_chunk_ids:
            assert result.items == ()
        else:
            assert 0 < len(result.items) <= 5
        assert response.headers["cache-control"] == "private, no-store"
