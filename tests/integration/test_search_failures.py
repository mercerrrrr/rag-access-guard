from pathlib import Path
from typing import override

import pytest
from fastapi.testclient import TestClient

from rag_access_guard_api.adapters import embeddings
from rag_access_guard_api.adapters.tokenizer import E5TokenCounter, TokenizerUnavailableError
from rag_access_guard_api.services import search
from tests.integration.embedding_fixtures import DeterministicEmbedder
from tests.integration.search_fixtures import configure_search


@pytest.mark.parametrize(
    "vector", [(float("nan"),) * 384, (0.0,) * 384, (1.0,)], ids=["nan", "zero", "dimension"]
)
def test_invalid_model_output_is_service_failure(
    admin_client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    vector: tuple[float, ...],
) -> None:
    configure_search(tmp_path / "config.json", monkeypatch)

    class BrokenEmbedder(DeterministicEmbedder):
        @override
        async def embed_query(self, text: str) -> tuple[float, ...]:
            assert text
            return vector

    monkeypatch.setattr(embeddings, "get_embedding_adapter", BrokenEmbedder)
    response = admin_client.post(
        "/api/search",
        json={"query": "PRIVATE_QUERY"},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 503
    assert "PRIVATE_QUERY" not in response.text
    assert response.headers["cache-control"] == "private, no-store"


def test_tokenizer_failure_is_sanitized_service_failure(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable() -> E5TokenCounter:
        raise TokenizerUnavailableError

    monkeypatch.setattr(search, "get_tokenizer", unavailable)
    response = admin_client.post(
        "/api/search",
        json={"query": "PRIVATE_QUERY"},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 503
    assert "PRIVATE_QUERY" not in response.text
