from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from rag_access_guard_api.schemas.documents import DocumentSummary
from tests.integration.chunk_fixtures import copy_stored_version


@pytest.mark.parametrize("corrupted_ordinal", [0, 1])
def test_sealing_rejects_substituted_body_or_overlap(
    admin_client: TestClient,
    auth_database: Engine,
    corrupted_ordinal: int,
) -> None:
    response = admin_client.post(
        "/api/admin/documents",
        data={"title": "Synthetic long source"},
        files={"file": ("long.txt", b"test " * 1000, "text/plain")},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 201
    document = DocumentSummary.model_validate_json(response.content)
    assert document.active_version_id is not None
    with auth_database.begin() as connection:
        target = uuid4()
        copy_stored_version(connection, document.active_version_id, target)
        _ = connection.execute(
            text("""WITH changed AS (
            SELECT *, CASE WHEN ordinal=:ordinal THEN 'X' || substring(text FROM 2)
                ELSE text END AS body FROM document_chunks WHERE document_version_id=:source
            ) INSERT INTO document_chunks
            (id,document_id,document_version_id,ordinal,text,content_sha256,
             token_count,char_start,char_end)
            SELECT gen_random_uuid(),document_id,:target,ordinal,body,
                encode(sha256(convert_to(body,'UTF8')),'hex'),token_count,char_start,char_end
            FROM changed"""),
            {"source": document.active_version_id, "target": target, "ordinal": corrupted_ordinal},
        )
        with pytest.raises(IntegrityError, match="canonical"), connection.begin_nested():
            _ = connection.execute(
                text("UPDATE document_versions SET status='chunked' WHERE id=:id"),
                {"id": target},
            )
