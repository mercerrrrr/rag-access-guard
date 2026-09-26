from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from httpx2 import Response
from sqlalchemy import text

from rag_access_guard import PolicySnapshot, SourceRef
from rag_access_guard_api.adapters.policy import PostgresPolicyReader
from rag_access_guard_api.schemas.chat import MessageRequest, SourceView
from rag_access_guard_api.services.sources import build_source_url
from tests.support.chat import ChatHttp
from tests.support.chat_generation import ChatCase


@pytest.fixture
def source(chat_case: ChatCase) -> SourceView:
    result = chat_case.send(
        MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="Question")
    )
    assert result.turn.state == "available"
    return result.turn.sources[0]


def assert_hidden(response: Response) -> None:
    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["vary"] == "Cookie"
    assert "location" not in response.headers
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("field", ["document_id", "document_version_id", "chunk_id"])
def test_unknown_witness_is_hidden(chat_case: ChatCase, source: SourceView, field: str) -> None:
    values = {
        "document_id": source.document_id,
        "document_version_id": source.document_version_id,
        "chunk_id": source.chunk_id,
    }
    values[field] = uuid4()
    assert_hidden(chat_case.client.get(build_source_url(SourceRef(**values))))


@pytest.mark.parametrize("mode", ["missing", "logout", "absolute", "idle", "inactive"])
def test_source_session_denial_is_not_an_auth_oracle(
    chat_case: ChatCase, source: SourceView, mode: str
) -> None:
    if mode == "missing":
        chat_case.client.cookies.clear()
    elif mode == "logout":
        assert (
            chat_case.client.post(
                "/api/auth/logout", headers=ChatHttp.csrf(chat_case.client)
            ).status_code
            == 204
        )
    else:
        statements = {
            "absolute": """UPDATE sessions SET created_at=clock_timestamp()-interval '9 hours',
                last_seen_at=clock_timestamp()-interval '5 seconds',
                absolute_expires_at=clock_timestamp()-interval '1 second'""",
            "idle": """UPDATE sessions SET created_at=clock_timestamp()-interval '1 hour',
                last_seen_at=clock_timestamp()-interval '31 minutes'""",
            "inactive": "UPDATE users SET is_active=false",
        }
        with chat_case.database.begin() as connection:
            _ = connection.execute(text(statements[mode]))
    assert_hidden(chat_case.client.get(source.url))
    assert chat_case.client.get("/api/auth/me").status_code == 401


@pytest.mark.parametrize("mode", ["outage", "missing", "extra"])
def test_source_denies_failed_or_incomplete_policy(
    chat_case: ChatCase, source: SourceView, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    original = PostgresPolicyReader.snapshot

    async def broken(
        self: PostgresPolicyReader,
        principal_id: UUID,
        refs: tuple[SourceRef, ...],
        *,
        thread_id: UUID | None = None,
    ) -> PolicySnapshot:
        if mode == "outage":
            message = "PRIVATE_POLICY_FAILURE"
            raise RuntimeError(message)
        snapshot = await original(self, principal_id, refs, thread_id=thread_id)
        allowed = (
            ()
            if mode == "missing"
            else (
                *snapshot.allowed_refs,
                SourceRef(document_id=uuid4(), document_version_id=uuid4(), chunk_id=uuid4()),
            )
        )
        return replace(snapshot, allowed_refs=allowed)

    monkeypatch.setattr(PostgresPolicyReader, "snapshot", broken)
    assert_hidden(chat_case.client.get(source.url))


def test_head_and_range_do_not_bypass_authorization(
    chat_case: ChatCase, source: SourceView
) -> None:
    allowed = chat_case.client.get(source.url, headers={"Range": "bytes=0-3"})
    assert allowed.status_code == 200
    assert allowed.json()["text"] == "PROTECTED_SYNTHETIC"
    assert "content-range" not in allowed.headers
    chat_case.revoke()
    assert_hidden(chat_case.client.get(source.url, headers={"Range": "bytes=0-3"}))
    unknown = build_source_url(
        SourceRef(document_id=uuid4(), document_version_id=uuid4(), chunk_id=uuid4())
    )
    for url in (source.url, unknown):
        response = chat_case.client.head(url)
        assert response.status_code == 405
        assert response.content == b""
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["vary"] == "Cookie"
        assert "etag" not in response.headers
        assert "last-modified" not in response.headers


def test_source_input_validation_has_no_protected_fields(
    chat_case: ChatCase, source: SourceView
) -> None:
    for url in (
        source.url.split("?")[0],
        source.url.replace(str(source.chunk_id), "bad"),
        source.url.replace(str(source.document_id), "bad"),
    ):
        response = chat_case.client.get(url)
        assert response.status_code == 422
        assert response.json() == {"detail": "Invalid request"}
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["vary"] == "Cookie"


@pytest.mark.parametrize("mode", ["new_version", "inactive"])
def test_old_source_closes_after_document_change(
    chat_case: ChatCase, source: SourceView, mode: str
) -> None:
    headers = ChatHttp.csrf(chat_case.client)
    if mode == "new_version":
        response = chat_case.client.post(
            f"/api/admin/documents/{source.document_id}/versions",
            files={"file": ("new.txt", b"NEW_SYNTHETIC", "text/plain")},
            headers=headers,
        )
        assert response.status_code == 201, response.text
    else:
        response = chat_case.client.patch(
            f"/api/admin/documents/{source.document_id}", json={"is_active": False}, headers=headers
        )
        assert response.status_code == 200, response.text
    assert_hidden(chat_case.client.get(source.url))
    turn = chat_case.read().turns[0]
    assert (turn.state, turn.answer, turn.sources) == ("unavailable", None, ())
