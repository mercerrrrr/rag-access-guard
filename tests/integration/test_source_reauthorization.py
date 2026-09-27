from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from rag_access_guard import PolicySnapshot, SourceRef
from rag_access_guard_api.adapters.policy import PostgresPolicyReader
from rag_access_guard_api.services.sources import build_source_url
from tests.integration.test_source_boundaries import assert_hidden
from tests.support.chat import ChatHttp
from tests.support.downloads import DownloadCase, source_ref


@pytest.mark.parametrize("surface", ["content", "original"])
@pytest.mark.parametrize("change", ["revoke", "version", "inactive"])
def test_old_url_reauthorizes_conditional_and_range_requests(
    download_case: DownloadCase, surface: str, change: str
) -> None:
    case = download_case.case
    url = download_case.content_url.replace("/content?", f"/{surface}?")
    assert case.client.get(url).status_code == 200
    if change == "revoke":
        case.revoke()
    elif change == "inactive":
        assert (
            case.client.patch(
                f"/api/admin/documents/{case.document.id}",
                json={"is_active": False},
                headers=ChatHttp.csrf(case.client),
            ).status_code
            == 200
        )
    else:
        result = case.client.post(
            f"/api/admin/documents/{case.document.id}/versions",
            files={"file": ("new.txt", b"NEW_SYNTHETIC", "text/plain")},
            headers=ChatHttp.csrf(case.client),
        )
        assert result.status_code == 201
        current = build_source_url(source_ref(case, case.document.id)).replace(
            "/content?", f"/{surface}?"
        )
        assert case.client.get(current).status_code == 200
    for headers in (
        {},
        {"If-None-Match": "*"},
        {"If-Modified-Since": "Wed, 01 Jan 2031 00:00:00 GMT"},
        {"Range": "bytes=0-2"},
    ):
        assert_hidden(case.client.get(url, headers=headers))


@pytest.mark.parametrize("mode", ["missing", "logout", "absolute", "idle", "inactive", "outage"])
def test_original_session_and_policy_failures_are_indistinguishable(
    download_case: DownloadCase, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    case = download_case.case
    if mode == "missing":
        case.client.cookies.clear()
    elif mode == "logout":
        assert (
            case.client.post("/api/auth/logout", headers=ChatHttp.csrf(case.client)).status_code
            == 204
        )
    elif mode == "outage":

        async def failed(
            _reader: PostgresPolicyReader,
            _principal: UUID,
            _refs: tuple[SourceRef, ...],
            *,
            thread_id: UUID | None = None,
        ) -> PolicySnapshot:
            del thread_id
            message = "PRIVATE_POLICY_FAILURE"
            raise ConnectionError(message)

        monkeypatch.setattr(PostgresPolicyReader, "snapshot", failed)
    else:
        statements = {
            "absolute": """UPDATE sessions SET created_at=clock_timestamp()-interval '9 hours',
                last_seen_at=clock_timestamp()-interval '5 seconds',
                absolute_expires_at=clock_timestamp()-interval '1 second'""",
            "idle": """UPDATE sessions SET created_at=clock_timestamp()-interval '1 hour',
                last_seen_at=clock_timestamp()-interval '31 minutes'""",
            "inactive": "UPDATE users SET is_active=false",
        }
        with case.database.begin() as connection:
            _ = connection.execute(text(statements[mode]))
    assert_hidden(case.client.get(download_case.original_url, headers={"Range": "bytes=0-2"}))
    if mode != "outage":
        assert case.client.get("/api/auth/me").status_code == 401


def test_original_validation_and_head_never_disclose_resource_metadata(
    download_case: DownloadCase,
) -> None:
    case = download_case.case
    url = download_case.original_url
    unknown = url.replace(str(download_case.ref.chunk_id), str(uuid4()))
    assert_hidden(case.client.get(unknown))
    for target in (url.split("?")[0], url.replace(str(download_case.ref.chunk_id), "bad")):
        response = case.client.get(target)
        assert response.status_code == 422
        assert response.json() == {"detail": "Invalid request"}
    for target in (url, unknown):
        response = case.client.head(target)
        assert response.status_code == 405
        assert response.content == b""
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["vary"] == "Cookie"
        assert not {"etag", "last-modified", "content-disposition"} & response.headers.keys()
