from pathlib import Path
from typing import override

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from rag_access_guard_api.adapters import embeddings
from rag_access_guard_api.schemas.access import GrantView
from tests.integration.embedding_fixtures import DeterministicEmbedder
from tests.integration.search_fixtures import configure_search


@pytest.mark.parametrize(
    "case",
    [
        ("UPDATE sessions SET revoked_at=clock_timestamp()", 401),
        ("UPDATE users SET is_active=false", 401),
        ("UPDATE sessions SET csrf_token_digest=decode(repeat('ab',32),'hex')", 403),
        ("DELETE FROM document_grants", 200),
    ],
)
def test_session_revoked_during_query_embedding_is_denied(  # noqa: PLR0913
    admin_client: TestClient,
    auth_database: Engine,
    self_grant: GrantView,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    case: tuple[str, int],
) -> None:
    assert self_grant.user_id is not None
    configure_search(tmp_path / "config.json", monkeypatch)
    change, status = case

    class RevokingEmbedder(DeterministicEmbedder):
        @override
        async def embed_query(self, text: str) -> tuple[float, ...]:
            with auth_database.begin() as connection:
                _ = connection.exec_driver_sql(
                    "SELECT revision FROM policy_state FOR UPDATE NOWAIT"
                )
                _ = connection.exec_driver_sql(change)
                _ = connection.exec_driver_sql("UPDATE policy_state SET revision=revision+1")
            return await super().embed_query(text)

    monkeypatch.setattr(embeddings, "get_embedding_adapter", RevokingEmbedder)
    response = admin_client.post(
        "/api/search",
        json={"query": "question"},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == status
    assert "PROTECTED_SYNTHETIC" not in response.text
    assert response.headers["cache-control"] == "private, no-store"
    if status == 200:
        assert response.json() == {"items": []}
