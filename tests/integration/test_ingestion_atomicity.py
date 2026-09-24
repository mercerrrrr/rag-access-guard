import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text

from rag_access_guard_api.persistence import PolicyState
from rag_access_guard_api.schemas.documents import DocumentSummary
from rag_access_guard_api.schemas.ingestion import UploadPayload
from rag_access_guard_api.services import ingestion


@pytest.mark.parametrize(
    "case",
    [
        ("UPDATE sessions SET revoked_at=clock_timestamp()", 401),
        ("UPDATE users SET is_admin=false", 403),
        ("UPDATE sessions SET csrf_token_digest=decode(repeat('ab',32),'hex')", 403),
    ],
    ids=["session-revoked", "admin-demoted", "csrf-rotated"],
)
def test_session_revoked_during_parse_prevents_write(
    admin_client: TestClient,
    auth_database: Engine,
    registered_document: DocumentSummary,
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[str, int],
) -> None:
    change, status = case
    prepare = ingestion.prepare_upload

    def parse_and_change_policy(upload: UploadPayload) -> ingestion.PreparedUpload:
        result = prepare(upload)
        with auth_database.begin() as connection:
            _ = connection.execute(text("SELECT revision FROM policy_state FOR UPDATE NOWAIT"))
            _ = connection.execute(text(change))
        return result

    monkeypatch.setattr(ingestion, "prepare_upload", parse_and_change_policy)
    with auth_database.connect() as connection:
        revision = connection.execute(select(PolicyState.revision)).scalar_one()
    response = admin_client.post(
        f"/api/admin/documents/{registered_document.id}/versions",
        files={"file": ("new.txt", b"new text", "text/plain")},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == status
    with auth_database.connect() as connection:
        assert connection.execute(select(PolicyState.revision)).scalar_one() == revision
        assert connection.execute(text("SELECT count(*) FROM document_versions")).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM document_chunks")).scalar_one() == 1
        assert (
            connection.execute(text("SELECT active_version_id FROM documents")).scalar_one()
            == registered_document.active_version_id
        )


def test_audit_failure_rolls_back_new_version(
    admin_client: TestClient, auth_database: Engine, registered_document: DocumentSummary
) -> None:
    with auth_database.begin() as connection:
        revision = connection.execute(select(PolicyState.revision)).scalar_one()
        _ = connection.execute(
            text("""CREATE FUNCTION reject_test_audit() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'test audit failure'; END $$""")
        )
        _ = connection.execute(
            text("""CREATE TRIGGER reject_test_audit BEFORE INSERT ON audit_events
            FOR EACH ROW EXECUTE FUNCTION reject_test_audit()""")
        )
    response = admin_client.post(
        f"/api/admin/documents/{registered_document.id}/versions",
        files={"file": ("new.txt", b"new text", "text/plain")},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "Service unavailable"}
    with auth_database.connect() as connection:
        assert connection.execute(select(PolicyState.revision)).scalar_one() == revision
        assert connection.execute(text("SELECT count(*) FROM document_versions")).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM document_chunks")).scalar_one() == 1
        assert (
            connection.execute(text("SELECT active_version_id FROM documents")).scalar_one()
            == registered_document.active_version_id
        )
