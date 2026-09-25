import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from rag_access_guard_api.config import Settings
from rag_access_guard_api.database import create_database_engine
from rag_access_guard_api.schemas.documents import DocumentSummary, DocumentVersionSummary
from rag_access_guard_api.server import create_event_loop
from rag_access_guard_api.services.indexing import index_document_version
from tests.helpers.pdf_factory import text_pdf
from tests.integration.embedding_fixtures import DeterministicEmbedder


@pytest.mark.parametrize("pdf", [False, True], ids=["text", "pdf"])
def test_model_revision_change_creates_new_document_version(
    registered_document: DocumentSummary,
    admin_client: TestClient,
    auth_database: Engine,
    *,
    pdf: bool,
) -> None:
    if pdf:
        response = admin_client.post(
            f"/api/admin/documents/{registered_document.id}/versions",
            files={"file": ("source.pdf", text_pdf(("PDF source",)), "application/pdf")},
            headers={
                "Origin": "https://rag.test",
                "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
            },
        )
        assert response.status_code == 201
        registered_document = registered_document.model_copy(
            update={
                "active_version_id": DocumentVersionSummary.model_validate_json(response.content).id
            }
        )
    source = registered_document.active_version_id
    assert source is not None
    with auth_database.connect() as connection:
        before = connection.execute(
            text("SELECT * FROM document_versions WHERE id=:id"), {"id": source}
        ).one()

    async def run() -> DocumentVersionSummary:
        engine = create_database_engine(Settings())
        try:
            return await index_document_version(
                engine,
                admin_client.cookies["__Host-rag_session"],
                registered_document.id,
                source,
                embedder=DeterministicEmbedder(revision="a" * 40),
                csrf_token=admin_client.cookies["__Host-rag_csrf"],
            )
        finally:
            await engine.dispose()

    version = anyio.run(run, backend_options={"loop_factory": create_event_loop})
    assert version.id != source
    assert version.status == "ready"
    with auth_database.connect() as connection:
        assert (
            connection.execute(
                text("SELECT * FROM document_versions WHERE id=:id"), {"id": source}
            ).one()
            == before
        )
        assert (
            connection.execute(
                text(
                    """SELECT ingestion_manifest->>'embedding_model_revision'
                    FROM document_versions WHERE id=:id"""
                ),
                {"id": version.id},
            ).scalar_one()
            == "a" * 40
        )
