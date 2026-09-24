from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, insert, update

from rag_access_guard_api.persistence import Document, DocumentGrant, Role, UserRole
from rag_access_guard_api.schemas.access import AccessibleDocuments, DocumentText, GrantView
from rag_access_guard_api.schemas.documents import DocumentSummary
from tests.integration.chunk_fixtures import copy_chunked_version


def test_admin_without_grant_cannot_read_content(
    admin_client: TestClient,
    registered_document: DocumentSummary,
) -> None:
    # Given: the document creator is an administrator without a content grant.
    path = (
        f"/api/documents/{registered_document.id}"
        f"/versions/{registered_document.active_version_id}/text"
    )
    # When: the creator follows a known version URL.
    response = admin_client.get(path)
    # Then: the response is indistinguishable from an unknown document.
    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}
    assert "PROTECTED_SYNTHETIC" not in response.text
    assert response.headers["cache-control"] == "private, no-store"


def test_list_without_grant_does_not_reveal_creator_title(
    admin_client: TestClient, registered_document: DocumentSummary
) -> None:
    response = admin_client.get("/api/documents")
    assert response.status_code == 200
    assert AccessibleDocuments.model_validate_json(response.content).items == ()
    assert registered_document.title not in response.text


def test_direct_grant_allows_only_active_version(
    admin_client: TestClient,
    self_grant: GrantView,
    registered_document: DocumentSummary,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = (
        f"/api/documents/{self_grant.document_id}"
        f"/versions/{registered_document.active_version_id}/text"
    )
    response = admin_client.get(path)
    assert response.status_code == 200
    actual = DocumentText.model_validate_json(response.content)
    assert (actual.document_id, actual.document_version_id, actual.text) == (
        registered_document.id,
        registered_document.active_version_id,
        "PROTECTED_SYNTHETIC",
    )
    listed = AccessibleDocuments.model_validate_json(admin_client.get("/api/documents").content)
    assert [(item.id, item.title, item.active_version_id) for item in listed.items] == [
        (registered_document.id, "Synthetic", registered_document.active_version_id)
    ]
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["vary"] == "Cookie"
    assert "PROTECTED_SYNTHETIC" not in caplog.text


def test_revoked_url_and_random_ids_have_same_denial(
    admin_client: TestClient, self_grant: GrantView, registered_document: DocumentSummary
) -> None:
    headers = {
        "Origin": "https://rag.test",
        "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
    }
    assert (
        admin_client.delete(
            f"/api/admin/documents/{self_grant.document_id}/grants/{self_grant.id}", headers=headers
        ).status_code
        == 204
    )
    old = admin_client.get(
        f"/api/documents/{self_grant.document_id}/versions/{registered_document.active_version_id}/text"
    )
    unknown = admin_client.get(f"/api/documents/{uuid4()}/versions/{uuid4()}/text")
    assert old.status_code == unknown.status_code == 404
    assert old.content == unknown.content == b'{"detail":"Not found"}'
    assert old.headers["cache-control"] == unknown.headers["cache-control"] == "private, no-store"
    assert old.headers["vary"] == unknown.headers["vary"] == "Cookie"
    assert admin_client.get("/api/documents").json() == {"items": []}


def test_cross_document_version_is_denied(
    admin_client: TestClient,
    auth_database: Engine,
    self_grant: GrantView,
    registered_document: DocumentSummary,
) -> None:
    other_id = uuid4()
    with auth_database.begin() as connection:
        _ = connection.execute(
            insert(Document).values(id=other_id, title="Other", created_by=self_grant.user_id)
        )
        _ = connection.execute(
            insert(DocumentGrant).values(
                id=uuid4(),
                document_id=other_id,
                user_id=self_grant.user_id,
                created_by=self_grant.user_id,
            )
        )
    response = admin_client.get(
        f"/api/documents/{other_id}/versions/{registered_document.active_version_id}/text"
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}


@pytest.mark.parametrize("active", [False, True])
def test_list_and_read_use_same_predicate(
    admin_client: TestClient,
    auth_database: Engine,
    self_grant: GrantView,
    registered_document: DocumentSummary,
    *,
    active: bool,
) -> None:
    with auth_database.begin() as connection:
        _ = connection.execute(update(Document).values(is_active=active))
    response = admin_client.get(
        f"/api/documents/{self_grant.document_id}/versions/{registered_document.active_version_id}/text"
    )
    listed = AccessibleDocuments.model_validate_json(admin_client.get("/api/documents").content)
    assert response.status_code == (200 if active else 404)
    assert len(listed.items) == (1 if active else 0)


def test_old_version_is_denied_after_active_switch(
    admin_client: TestClient,
    auth_database: Engine,
    self_grant: GrantView,
    registered_document: DocumentSummary,
) -> None:
    version_id = uuid4()
    assert registered_document.active_version_id is not None
    with auth_database.begin() as connection:
        copy_chunked_version(connection, registered_document.active_version_id, version_id)
        _ = connection.execute(
            update(Document)
            .where(Document.id == self_grant.document_id)
            .values(active_version_id=version_id)
        )
    old = admin_client.get(
        f"/api/documents/{self_grant.document_id}/versions/{registered_document.active_version_id}/text"
    )
    assert old.status_code == 404
    assert old.json() == {"detail": "Not found"}
    assert (
        admin_client.get(
            f"/api/documents/{self_grant.document_id}/versions/{version_id}/text"
        ).status_code
        == 200
    )


def test_revoke_one_grant_preserves_other_allow_path(
    admin_client: TestClient,
    auth_database: Engine,
    self_grant: GrantView,
    registered_document: DocumentSummary,
) -> None:
    role_id = uuid4()
    with auth_database.begin() as connection:
        _ = connection.execute(
            insert(Role).values(id=role_id, code="reader_role", display_name="Reader role")
        )
        _ = connection.execute(insert(UserRole).values(user_id=self_grant.user_id, role_id=role_id))
        _ = connection.execute(
            insert(DocumentGrant).values(
                id=uuid4(),
                document_id=self_grant.document_id,
                role_id=role_id,
                created_by=self_grant.user_id,
            )
        )
    response = admin_client.delete(
        f"/api/admin/documents/{self_grant.document_id}/grants/{self_grant.id}",
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 204
    assert (
        admin_client.get(
            f"/api/documents/{self_grant.document_id}/versions/{registered_document.active_version_id}/text"
        ).status_code
        == 200
    )
    assert (
        len(
            AccessibleDocuments.model_validate_json(
                admin_client.get("/api/documents").content
            ).items
        )
        == 1
    )


def test_user_input_cannot_select_another_principal(authenticated_client: TestClient) -> None:
    response = authenticated_client.get(
        "/api/documents", params={"user_id": str(uuid4()), "is_admin": "true"}
    )
    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_no_session_is_denied_before_resource_lookup(auth_client: TestClient) -> None:
    response = auth_client.get(f"/api/documents/{uuid4()}/versions/{uuid4()}/text")
    assert response.status_code == 401
    assert response.headers["cache-control"] == "private, no-store"
    assert auth_client.get("/api/documents").status_code == 401
