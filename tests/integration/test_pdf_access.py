from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text

from rag_access_guard_api.adapters import embeddings
from rag_access_guard_api.adapters.tokenizer import MODEL_REVISION
from rag_access_guard_api.persistence import User
from rag_access_guard_api.schemas.documents import DocumentSummary, DocumentVersionSummary
from rag_access_guard_api.schemas.search import SearchResponse
from tests.helpers.pdf_factory import text_pdf
from tests.integration.search_fixtures import configure_search


def test_pdf_chunks_retrieve_only_for_granted_reader(
    admin_client: TestClient,
    auth_database: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = embeddings.E5EmbeddingAdapter(Path(f".cache/e5/{MODEL_REVISION}"))
    monkeypatch.setattr(embeddings, "get_embedding_adapter", lambda: real)
    configure_search(tmp_path / "calibration.json", monkeypatch, threshold=0.0)
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
    }
    source = "Удалённая работа требует подключения через корпоративный VPN."
    uploaded = admin_client.post(
        "/api/admin/documents",
        data={"title": "Synthetic PDF"},
        files={
            "file": (
                "source.pdf",
                text_pdf((source, "Use corporate VPN for remote work.")),
                "application/pdf",
            )
        },
        headers=headers,
    )
    assert uploaded.status_code == 201
    document = DocumentSummary.model_validate_json(uploaded.content)
    url = f"/api/documents/{document.id}/versions/{document.active_version_id}/text"
    assert admin_client.get(url).status_code == 404
    denied = admin_client.post("/api/search", json={"query": source}, headers=headers)
    assert denied.status_code == 200
    assert SearchResponse.model_validate_json(denied.content).items == ()
    with auth_database.connect() as connection:
        principal = connection.execute(select(User.id).where(User.login == "reader")).scalar_one()
    assert (
        admin_client.post(
            f"/api/admin/documents/{document.id}/grants",
            json={"user_id": str(principal)},
            headers=headers,
        ).status_code
        == 201
    )
    for query in (source, "How do employees connect for remote work?"):
        response = admin_client.post("/api/search", json={"query": query}, headers=headers)
        assert response.status_code == 200
        items = SearchResponse.model_validate_json(response.content).items
        assert items
        assert source in response.text
    assert admin_client.get(url).status_code == 200
    with auth_database.begin() as connection:
        _ = connection.execute(text("DELETE FROM document_grants"))
    assert admin_client.get(url).status_code == 404
    assert admin_client.post("/api/search", json={"query": source}, headers=headers).json() == {
        "items": []
    }


def test_pdf_reingestion_preserves_old_version_and_plain_text(
    admin_client: TestClient,
    auth_database: Engine,
) -> None:
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
    }
    raw = text_pdf(("<script>alert(1)</script>",))
    uploaded = admin_client.post(
        "/api/admin/documents",
        data={"title": "PDF"},
        files={"file": ("a.pdf", raw, "application/pdf")},
        headers=headers,
    )
    assert uploaded.status_code == 201
    document = DocumentSummary.model_validate_json(uploaded.content)
    with auth_database.connect() as connection:
        before = connection.execute(text("SELECT * FROM document_versions")).one()
        principal = connection.execute(select(User.id).where(User.login == "reader")).scalar_one()
    assert (
        admin_client.post(
            f"/api/admin/documents/{document.id}/grants",
            json={"user_id": str(principal)},
            headers=headers,
        ).status_code
        == 201
    )
    old_url = f"/api/documents/{document.id}/versions/{document.active_version_id}/text"
    shown = admin_client.get(old_url)
    assert shown.status_code == 200
    assert shown.headers["content-type"] == "application/json"
    assert shown.headers["x-content-type-options"] == "nosniff"
    assert shown.headers["cache-control"] == "private, no-store"
    assert shown.json()["text"] == "<script>alert(1)</script>"
    updated = admin_client.post(
        f"/api/admin/documents/{document.id}/versions",
        files={"file": ("a.pdf", raw, "application/pdf")},
        headers=headers,
    )
    assert updated.status_code == 201
    version = DocumentVersionSummary.model_validate_json(updated.content)
    assert version.id != document.active_version_id
    with auth_database.connect() as connection:
        assert (
            connection.execute(
                text("SELECT * FROM document_versions WHERE id=:id"),
                {"id": document.active_version_id},
            ).one()
            == before
        )
    assert admin_client.get(old_url).status_code == 404
