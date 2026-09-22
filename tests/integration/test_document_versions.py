from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from rag_access_guard_api.schemas.documents import DocumentSummary


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE document_versions SET original_bytes = original_bytes",
        "UPDATE document_versions SET extracted_text = extracted_text",
        "UPDATE document_versions SET content_sha256 = content_sha256",
        "UPDATE document_versions SET text_sha256 = text_sha256",
        "UPDATE document_versions SET parser_revision = parser_revision",
        "UPDATE document_versions SET document_id = document_id",
        "DELETE FROM document_versions",
    ],
)
def test_version_bytes_text_hash_are_immutable(
    registered_document: DocumentSummary,
    auth_database: Engine,
    statement: str,
) -> None:
    assert registered_document.active_version_id is not None
    with auth_database.begin() as connection, pytest.raises(IntegrityError, match="immutable"):
        _ = connection.execute(text(statement))


def test_active_version_must_belong_to_document(
    registered_document: DocumentSummary,
    auth_database: Engine,
) -> None:
    with (
        auth_database.begin() as connection,
        pytest.raises(IntegrityError, match="fk_documents_active_version"),
    ):
        _ = connection.execute(
            text("""INSERT INTO documents (id, title, created_by, active_version_id)
            SELECT :id, 'Other', created_by, active_version_id
            FROM documents WHERE id = :source"""),
            {"id": uuid4(), "source": registered_document.id},
        )


def test_old_documents_without_version_remain_unreadable(
    admin_client: TestClient,
    auth_database: Engine,
) -> None:
    document_id = uuid4()
    with auth_database.begin() as connection:
        _ = connection.execute(
            text(
                """INSERT INTO documents (id, title, created_by)
                SELECT :id, 'Legacy', id FROM users LIMIT 1"""
            ),
            {"id": document_id},
        )
    response = admin_client.get(f"/api/admin/documents/{document_id}/versions")
    assert response.status_code == 200
    assert response.json() == {"items": []}
    assert admin_client.get(f"/api/documents/{document_id}/content").status_code == 404
