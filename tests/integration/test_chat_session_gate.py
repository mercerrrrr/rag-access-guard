from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from rag_access_guard_api.main import create_app
from rag_access_guard_api.schemas.chat import ThreadView
from rag_access_guard_api.server import create_event_loop
from rag_access_guard_api.services.security import ReadUoW
from tests.integration.test_document_read_ordering import wait_for_policy_wait
from tests.support.chat import ChatHttp


def test_waiting_chat_read_rechecks_session_after_policy_commit(chat_http: ChatHttp) -> None:
    created = ThreadView.model_validate_json(
        chat_http.owner.post(
            "/api/chat/threads", json={}, headers=chat_http.csrf(chat_http.owner)
        ).content
    )
    with chat_http.database.connect() as writer, ThreadPoolExecutor(max_workers=1) as executor:
        _ = writer.execute(text("SELECT revision FROM policy_state FOR UPDATE"))
        backend = TypeAdapter(int).validate_python(
            writer.execute(text("SELECT pg_backend_pid()")).scalar_one()
        )
        reader = executor.submit(chat_http.owner.get, f"/api/chat/threads/{created.id}")
        try:
            wait_for_policy_wait(chat_http.database, backend)
            _ = writer.execute(text("UPDATE sessions SET revoked_at=clock_timestamp()"))
            _ = writer.execute(text("UPDATE policy_state SET revision=revision+1"))
        finally:
            writer.commit()
        response = reader.result(10)
    assert response.status_code == 401
    assert response.json() == {"detail": "Unauthorized"}
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize(
    ("error", "status", "detail"),
    [
        (SQLAlchemyError("private database detail"), 503, "Service unavailable"),
        (RuntimeError("private application detail"), 500, "Internal server error"),
    ],
)
def test_chat_errors_are_sanitized_and_uncacheable(
    error: Exception,
    status: int,
    detail: str,
    chat_http: ChatHttp,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = Event()

    async def fail(_uow: ReadUoW) -> tuple[ThreadView, ...]:
        entered.set()
        raise error

    monkeypatch.setattr("rag_access_guard_api.routes.chat.list_threads", fail)
    with TestClient(
        create_app(),
        base_url="https://rag.test",
        raise_server_exceptions=False,
        backend_options={"loop_factory": create_event_loop},
    ) as client:
        client.cookies.update(chat_http.owner.cookies)
        response = client.get("/api/chat/threads")
    assert entered.is_set(), "The injected repository failure must be reached"
    assert response.status_code == status
    assert response.json() == {"detail": detail}
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["vary"] == "Cookie"
