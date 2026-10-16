from itertools import count
from pathlib import Path
from uuid import UUID

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.ext.asyncio import create_async_engine

from rag_access_guard_api.adapters import embeddings
from rag_access_guard_api.config import Settings
from rag_access_guard_api.demo.calibration import actor_top_five
from rag_access_guard_api.persistence import DocumentChunk, User
from rag_access_guard_api.schemas.documents import DocumentSummary
from rag_access_guard_api.server import create_event_loop
from rag_access_guard_api.services import ingestion
from rag_access_guard_api.services.calibration import ScoredQuery, score_thresholds
from rag_access_guard_api.services.retrieval import retrieve
from rag_access_guard_api.services.security import PolicyUnitOfWork
from tests.integration.search_fixtures import configure_search
from tests.integration.test_access_filtered_retrieval import RankedEmbedder


def test_actor_calibration_uses_production_ties_before_threshold_scoring(
    admin_client: TestClient,
    auth_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configure_search(tmp_path / "retrieval.json", monkeypatch)
    monkeypatch.setattr(embeddings, "get_embedding_adapter", RankedEmbedder)
    identities = count(100000, -1)

    def allocated_uuid() -> UUID:
        return UUID(int=next(identities))

    monkeypatch.setattr(ingestion, "uuid4", allocated_uuid)
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
    }
    uploaded = admin_client.post(
        "/api/admin/documents",
        data={"title": "Synthetic tie"},
        files={"file": ("tie.txt", b"ALLOWED_MARKER " * 2000, "text/plain")},
        headers=headers,
    )
    assert uploaded.status_code == 201
    document = DocumentSummary.model_validate_json(uploaded.content)
    with auth_database.connect() as connection:
        principal = connection.execute(select(User.id).where(User.login == "reader")).scalar_one()
        chunks = tuple(
            connection.execute(
                select(DocumentChunk.id).where(DocumentChunk.document_id == document.id)
            ).scalars()
        )
    assert len(chunks) > 5
    expected = tuple(sorted(chunks, reverse=True)[:5])
    grant = admin_client.post(
        f"/api/admin/documents/{document.id}/grants",
        json={"user_id": str(principal)},
        headers=headers,
    )
    assert grant.status_code == 201
    denied = admin_client.post(
        "/api/admin/documents",
        data={"title": "Denied tie"},
        files={"file": ("denied.txt", b"CLOSED_MARKER", "text/plain")},
        headers=headers,
    )
    assert denied.status_code == 201

    async def compare() -> None:
        engine = create_async_engine(str(Settings().database_url), hide_parameters=True)
        vector = (1.0, *(0.0 for _ in range(383)))
        try:
            async with PolicyUnitOfWork(engine).protected_read(
                admin_client.cookies["__Host-rag_session"]
            ) as uow:
                visible = await retrieve(uow, vector)
                scored = await actor_top_five(uow, vector)
            assert tuple(row.source_ref.chunk_id for row in visible) == expected
            assert tuple(row.chunk_id for row in scored) == expected
            selected = score_thresholds(
                (
                    ScoredQuery(
                        scores=tuple((row.chunk_id, row.score) for row in scored),
                        relevant=frozenset(expected),
                    ),
                )
            )
            assert (selected.chosen.tp, selected.chosen.fp, selected.chosen.fn) == (5, 0, 0)
        finally:
            await engine.dispose()

    anyio.run(compare, backend_options={"loop_factory": create_event_loop})
