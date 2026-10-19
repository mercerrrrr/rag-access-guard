from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import text

from rag_access_guard_api.main import create_app
from rag_access_guard_api.schemas.chat import MessageRequest, ThreadView
from rag_access_guard_api.schemas.chat_progress import ProgressStage, RequestStatus
from rag_access_guard_api.server import create_event_loop
from rag_access_guard_api.services.chat_progress import RequestProgressTracker
from tests.support.chat import ChatHttp
from tests.support.chat_generation import ChatCase


def status(client: TestClient, thread: UUID, request: UUID) -> Response:
    return client.get(
        f"/api/chat/threads/{thread}/requests/{request}/status",
        headers={"Origin": "https://rag.test"},
    )


def test_live_generation_status_has_no_content_and_becomes_terminal(chat_case: ChatCase) -> None:
    # Given: an accepted request held at the real model boundary.
    payload = MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="QUESTION")
    chat_case.model.hold = True
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(chat_case.send, payload)
        try:
            assert chat_case.model.entered.wait(10)
            # When: the owner checks the pending request.
            response = status(chat_case.client, chat_case.thread, payload.request_id)
            # Then: only the observed generation stage is exposed before commit.
            assert response.status_code == 200, response.text
            dto = RequestStatus.model_validate_json(response.content)
            assert (dto.state, dto.stage) == ("running", "generation")
            assert dto.elapsed_seconds is not None
            assert dto.elapsed_seconds >= 0
            assert set(dto.model_dump()) == {"request_id", "state", "stage", "elapsed_seconds"}
            assert "SYNTHETIC_ANSWER" not in response.text
            assert "QUESTION" not in response.text
            assert response.headers["cache-control"] == "private, no-store"
        finally:
            chat_case.model.resume.set()
        assert running.result(10).turn.state == "available"
    response = status(chat_case.client, chat_case.thread, payload.request_id)
    assert response.json() == {
        "request_id": str(payload.request_id),
        "state": "terminal",
        "stage": None,
        "elapsed_seconds": None,
    }
    assert chat_case.read().turns[0].answer == "SYNTHETIC_ANSWER"
    chat_case.revoke()
    assert status(chat_case.client, chat_case.thread, payload.request_id).json() == response.json()
    assert chat_case.read().turns[0].state == "unavailable"


def test_unknown_request_in_owned_thread_is_absent(chat_case: ChatCase) -> None:
    # Given: an owned thread with no reservation for this identifier.
    request = uuid4()
    # When: the owner requests status.
    response = status(chat_case.client, chat_case.thread, request)
    # Then: existence of the owned thread does not imply a request exists.
    assert response.status_code == 200
    assert response.json() == {
        "request_id": str(request),
        "state": "absent",
        "stage": None,
        "elapsed_seconds": None,
    }


@pytest.mark.parametrize("admin", [False, True])
def test_foreign_and_missing_threads_do_not_consult_tracker(
    chat_http: ChatHttp, monkeypatch: pytest.MonkeyPatch, *, admin: bool
) -> None:
    # Given: even an administrator has no ownership of another user's thread.
    created = ThreadView.model_validate_json(
        chat_http.owner.post(
            "/api/chat/threads", json={}, headers=ChatHttp.csrf(chat_http.owner)
        ).content
    )
    if admin:
        with chat_http.database.begin() as connection:
            _ = connection.execute(text("UPDATE users SET is_admin=true WHERE login='other'"))

    def forbidden_lookup(
        _self: RequestProgressTracker, _thread_id: UUID, _request_id: UUID
    ) -> ProgressStage | None:
        pytest.fail("Tracker read before ownership proof")

    monkeypatch.setattr(RequestProgressTracker, "get_stage", forbidden_lookup)
    # When: foreign and missing UUIDs are checked.
    foreign = status(chat_http.other, created.id, uuid4())
    missing = status(chat_http.other, uuid4(), uuid4())
    # Then: neither metadata existence nor tracker contents can distinguish them.
    assert foreign.status_code == missing.status_code == 404
    assert foreign.content == missing.content
    assert (
        foreign.headers["cache-control"] == missing.headers["cache-control"] == "private, no-store"
    )


def test_revoked_session_cannot_read_status(chat_case: ChatCase) -> None:
    # Given: a browser retaining a revoked session cookie.
    with chat_case.database.begin() as connection:
        _ = connection.execute(text("UPDATE sessions SET revoked_at=clock_timestamp()"))
    # When: it requests status.
    response = status(chat_case.client, chat_case.thread, uuid4())
    # Then: no state is disclosed.
    assert response.status_code == 401
    assert response.headers["cache-control"] == "private, no-store"


def test_fresh_app_recovers_live_lease_then_observes_expiry_without_writes(
    chat_case: ChatCase,
) -> None:
    # Given: a real pending reservation in another app's memory.
    payload = MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="QUESTION")
    chat_case.model.hold = True
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(chat_case.send, payload)
        try:
            assert chat_case.model.entered.wait(10)
            with TestClient(
                create_app(),
                base_url="https://rag.test",
                backend_options={"loop_factory": create_event_loop},
            ) as fresh:
                fresh.cookies.update(chat_case.client.cookies)
                # When: a fresh app observes the same database without the old tracker.
                response = status(fresh, chat_case.thread, payload.request_id)
                # Then: it reports recovery, never invented generation or a new model call.
                assert response.status_code == 200, response.text
                assert response.json()["stage"] == "recovery"
                with chat_case.database.begin() as connection:
                    _ = connection.execute(
                        text("""UPDATE chat_turns SET
                        lease_expires_at=clock_timestamp()-interval '1 second'""")
                    )
                    before = connection.execute(
                        text("""SELECT state,lease_expires_at,created_at,
                        answer FROM chat_turns""")
                    ).one()
                expired = status(fresh, chat_case.thread, payload.request_id)
                assert expired.json() == {
                    "request_id": str(payload.request_id),
                    "state": "expired",
                    "stage": None,
                    "elapsed_seconds": None,
                }
                with chat_case.database.connect() as connection:
                    assert (
                        connection.execute(
                            text("""SELECT state,lease_expires_at,created_at,
                        answer FROM chat_turns""")
                        ).one()
                        == before
                    )
                assert before[0] == "pending"
                assert chat_case.model.call_count == 1
        finally:
            chat_case.model.resume.set()
        assert running.result(10).turn.state == "neutral"


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"Origin": "https://rag.test"}, 200),
        ({"Sec-Fetch-Site": "same-origin"}, 200),
        ({}, 403),
        ({"Sec-Fetch-Site": "same-site"}, 403),
        ({"Sec-Fetch-Site": "cross-site"}, 403),
        ({"Sec-Fetch-Site": "none"}, 403),
        ({"Origin": "https://foreign.invalid", "Sec-Fetch-Site": "same-origin"}, 403),
    ],
)
def test_status_requires_exact_origin_or_browser_same_origin(
    chat_case: ChatCase, headers: dict[str, str], expected: int
) -> None:
    # Given: an authenticated owner with the specified request-origin proof.
    # When: the status route sees the headers.
    response = chat_case.client.get(
        f"/api/chat/threads/{chat_case.thread}/requests/{uuid4()}/status",
        headers=headers,
    )
    # Then: explicit foreign Origin always denies, and missing Origin requires same-origin fetch.
    assert response.status_code == expected
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize("suffix", ["?query=SECRET", "?", "/"])
def test_status_rejects_query_or_redirects_without_content(
    chat_case: ChatCase, suffix: str
) -> None:
    # Given: the UUID route is requested with an extra suffix.
    # When: strict query parsing or normal slash handling runs.
    response = chat_case.client.get(
        f"/api/chat/threads/{chat_case.thread}/requests/{uuid4()}/status{suffix}",
        headers={"Origin": "https://rag.test"},
    )
    # Then: nonempty query is rejected; empty delimiter and slash reveal no content.
    assert response.status_code == (422 if suffix.startswith("?query") else 200)
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize(
    ("thread", "request_id"), [("invalid", str(uuid4())), (str(uuid4()), "invalid")]
)
def test_status_rejects_non_uuid_paths(chat_case: ChatCase, thread: str, request_id: str) -> None:
    # Given/When: a UUID path parameter is malformed.
    response = chat_case.client.get(
        f"/api/chat/threads/{thread}/requests/{request_id}/status",
        headers={"Origin": "https://rag.test"},
    )
    # Then: sanitized validation exposes no request details.
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid request"}
    assert response.headers["cache-control"] == "private, no-store"
