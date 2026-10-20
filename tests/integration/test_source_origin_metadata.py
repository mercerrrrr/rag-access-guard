import pytest
from sqlalchemy import select, text

from rag_access_guard_api.persistence import PolicyState
from rag_access_guard_api.schemas.access import AccessibleDocuments, GrantView
from rag_access_guard_api.schemas.documents import DocumentList, DocumentVersionList
from rag_access_guard_api.schemas.sources import SourceContent
from rag_access_guard_api.services.sources import build_source_url
from tests.integration.test_document_origin import synthetic_origin, upload_origin
from tests.support.chat import ChatHttp
from tests.support.chat_generation import ChatCase
from tests.support.downloads import source_ref


def test_source_read_returns_origin_and_current_revision(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given an authorized immutable version with provenance.
    origin = synthetic_origin()
    ref = upload_origin(chat_case, monkeypatch, origin)
    with chat_case.database.connect() as connection:
        revision = connection.execute(select(PolicyState.revision)).scalar_one()
    # When the complete source tuple is opened.
    response = chat_case.client.get(build_source_url(ref))
    # Then provenance and the opening authorization revision are returned together.
    assert response.status_code == 200
    content = SourceContent.model_validate_json(response.content)
    assert content.origin == origin
    assert content.policy_revision == revision
    assert response.headers["cache-control"] == "private, no-store"


def test_source_read_uses_new_revision_after_grant_mutation(
    chat_case: ChatCase, role_grant: GrantView
) -> None:
    # Given independent direct and role grants for the same active version.
    assert role_grant.document_id == chat_case.document.id
    ref = source_ref(chat_case, chat_case.document.id)
    with chat_case.database.connect() as connection:
        previous = connection.execute(select(PolicyState.revision)).scalar_one()
    chat_case.revoke()
    with chat_case.database.connect() as connection:
        current = connection.execute(select(PolicyState.revision)).scalar_one()
    # When an old source URL is reopened through the surviving role grant.
    response = chat_case.client.get(build_source_url(ref))
    # Then it reports this read's revision even for a legacy null origin.
    assert current == previous + 1
    assert response.status_code == 200
    content = SourceContent.model_validate_json(response.content)
    assert content.policy_revision == current
    assert content.origin is None


def test_registry_origin_is_bound_to_authorized_active_version(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given two immutable versions with distinct provenance.
    origin = synthetic_origin()
    old = upload_origin(chat_case, monkeypatch, origin)
    current_origin = origin.model_copy(update={"transformation_revision": "authored-v2"})
    current = upload_origin(chat_case, monkeypatch, current_origin)
    # When the authorized administrator reads both metadata lists.
    documents = chat_case.client.get("/api/admin/documents")
    versions = chat_case.client.get(f"/api/admin/documents/{chat_case.document.id}/versions")
    # Then only the active, content-readable version exposes provenance.
    assert documents.status_code == versions.status_code == 200
    assert DocumentList.model_validate_json(documents.content).items[0].origin == current_origin
    projected = {
        item.id: item.origin
        for item in DocumentVersionList.model_validate_json(versions.content).items
    }
    assert projected[old.document_version_id] is None
    assert projected[current.document_version_id] == current_origin


def test_reader_list_exposes_only_authorized_active_origin(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a content grant and no administrative capability.
    origin = synthetic_origin()
    _ = upload_origin(chat_case, monkeypatch, origin)
    with chat_case.database.begin() as connection:
        _ = connection.execute(text("UPDATE users SET is_admin=false WHERE login='reader'"))
    # When the ordinary reader opens their allowed-document list.
    response = chat_case.client.get("/api/documents")
    # Then authorized origin is included without granting administrative visibility.
    assert response.status_code == 200
    assert AccessibleDocuments.model_validate_json(response.content).items[0].origin == origin
    assert chat_case.client.get("/api/admin/documents").status_code == 403


@pytest.mark.parametrize("change", ["revoke", "inactive"])
def test_registry_hides_origin_when_content_access_is_denied(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    # Given origin metadata that was initially available to a granted administrator.
    ref = upload_origin(chat_case, monkeypatch, synthetic_origin())
    if change == "revoke":
        chat_case.revoke()
    else:
        result = chat_case.client.patch(
            f"/api/admin/documents/{chat_case.document.id}",
            json={"is_active": False},
            headers=ChatHttp.csrf(chat_case.client),
        )
        assert result.status_code == 200
    # When administrative metadata and the old source are requested again.
    documents = chat_case.client.get("/api/admin/documents")
    versions = chat_case.client.get(f"/api/admin/documents/{chat_case.document.id}/versions")
    source = chat_case.client.get(build_source_url(ref))
    # Then administrative visibility never widens to protected provenance or content.
    assert documents.status_code == versions.status_code == 200
    assert DocumentList.model_validate_json(documents.content).items[0].origin is None
    assert all(
        item.origin is None
        for item in DocumentVersionList.model_validate_json(versions.content).items
    )
    assert chat_case.client.get("/api/documents").json() == {"items": []}
    assert "Учебный" not in documents.text + versions.text
    assert source.status_code == 404
    assert source.json() == {"detail": "Not found"}


def test_source_version_mismatch_does_not_disclose_origin(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a source whose version has been replaced.
    old = upload_origin(chat_case, monkeypatch, synthetic_origin())
    _ = upload_origin(chat_case, monkeypatch, synthetic_origin())
    # When its complete historical source tuple is opened.
    response = chat_case.client.get(build_source_url(old))
    # Then neither provenance nor revision is disclosed by the denial.
    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}
