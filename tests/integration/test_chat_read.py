from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from rag_access_guard_api.schemas.access import GrantView
from rag_access_guard_api.schemas.chat import ThreadDetail, ThreadView
from rag_access_guard_api.schemas.documents import DocumentSummary
from tests.integration.search_fixtures import configure_search
from tests.support.chat import ChatHttp


def test_saved_answer_is_masked_immediately_after_revoke(
    admin_client: TestClient,
    self_grant: GrantView,
    registered_document: DocumentSummary,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an owned chat and a readable, indexed synthetic source.
    configure_search(tmp_path / "calibration.json", monkeypatch)
    monkeypatch.setenv("RAG_ACCESS_GUARD_LLM_ADAPTER", "fake")
    headers = ChatHttp.csrf(admin_client)
    thread = ThreadView.model_validate_json(
        admin_client.post("/api/chat/threads", json={}, headers=headers).content
    ).id
    response = admin_client.post(
        f"/api/chat/threads/{thread}/messages",
        json={
            "request_id": str(uuid4()),
            "expected_thread_revision": 0,
            "user_input": "Что сказано в регламенте?",
        },
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["turn"]["state"] == "available"
    # When: the effective grant is revoked through the production API.
    revoked = admin_client.delete(
        f"/api/admin/documents/{registered_document.id}/grants/{self_grant.id}", headers=headers
    )
    assert revoked.status_code == 204
    # Then: the original user input remains, but no answer or source metadata is delivered.
    turn = ThreadDetail.model_validate_json(
        admin_client.get(f"/api/chat/threads/{thread}").content
    ).turns[0]
    assert turn.user_input == "Что сказано в регламенте?"
    assert (turn.state, turn.answer, turn.sources) == ("unavailable", None, ())
