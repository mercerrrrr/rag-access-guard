from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Event
from time import monotonic
from typing import override
from uuid import UUID, uuid4

import pytest
from anyio.to_thread import run_sync
from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic import TypeAdapter
from sqlalchemy import Boolean, Engine, Integer, column, func, select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from rag_access_guard_api.main import create_app
from rag_access_guard_api.persistence import ChatThread, ChatTurn
from rag_access_guard_api.routes import chat as routes
from rag_access_guard_api.schemas.auth import CsrfResponse
from rag_access_guard_api.schemas.chat import MessageRequest, ThreadDetail
from rag_access_guard_api.server import create_event_loop
from rag_access_guard_api.services import chat, security
from rag_access_guard_api.services.chat_repository import get_owned_thread
from rag_access_guard_api.services.chat_state import Reservation
from tests.support.chat import ChatHttp
from tests.support.chat_generation import ChatCase


def wait_for_thread_lock(engine: Engine, blocker: int, response: Future[Response]) -> bool:
    deadline = monotonic() + 10
    with engine.connect() as observer:
        while not response.done():
            waiting = TypeAdapter(bool).validate_python(
                observer.execute(
                    text("""SELECT EXISTS (
                SELECT 1 FROM pg_stat_activity WHERE datname=current_database()
                AND :blocker=ANY(pg_blocking_pids(pid)) AND wait_event_type='Lock'
                AND query LIKE '%chat_threads%' AND query LIKE '%FOR %') AS waiting""").columns(
                        column("waiting", Boolean)
                    ),
                    {"blocker": blocker},
                ).scalar_one()
            )
            observer.rollback()
            if waiting:
                return True
            assert monotonic() < deadline, "Thread wait or completed request was not observed"
    return False


@pytest.mark.parametrize("mode", ["replay", "new_request", "late_worker"])
def test_session_expiry_while_waiting_for_thread_denies_read_and_write(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    payload = MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="QUESTION")
    if mode == "replay":
        _ = chat_case.send(payload)
    completed, finish_worker, expired = Event(), Event(), Event()
    original_clock = security.database_clock

    class LateWorkerService(chat.ChatService):
        @override
        async def _generate(
            self, session_token: str, reservation: Reservation, user_input: str
        ) -> chat.Generated | chat.Neutral:
            outcome = await super()._generate(session_token, reservation, user_input)
            _ = await self._complete(session_token, outcome)
            completed.set()
            assert await run_sync(finish_worker.wait, 10)
            return outcome

    async def clock(connection: AsyncConnection) -> datetime:
        now = await original_clock(connection)
        return now + timedelta(hours=9) if expired.is_set() else now

    if mode == "late_worker":
        monkeypatch.setattr(routes, "ChatService", LateWorkerService)
    monkeypatch.setattr(security, "database_clock", clock)

    def post() -> Response:
        return chat_case.client.post(
            f"/api/chat/threads/{chat_case.thread}/messages",
            json=payload.model_dump(mode="json"),
            headers=ChatHttp.csrf(chat_case.client),
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending: Future[Response] | None = None
        if mode == "late_worker":
            pending = pool.submit(post)
            assert completed.wait(10)
        with chat_case.database.begin() as blocker:
            _ = blocker.execute(text("SELECT id FROM policy_state FOR SHARE"))
            _ = blocker.execute(
                text("SELECT id FROM chat_threads WHERE id=:id FOR UPDATE"),
                {"id": chat_case.thread},
            )
            pid = blocker.execute(select(func.pg_backend_pid(type_=Integer))).scalar_one()
            if mode != "late_worker":
                pending = pool.submit(post)
            else:
                finish_worker.set()
            assert pending is not None
            assert wait_for_thread_lock(chat_case.database, pid, pending)
            expired.set()
        response = pending.result(10)
    assert response.status_code == 401
    assert "SYNTHETIC_ANSWER" not in response.text
    assert chat_case.document.title not in response.text
    with chat_case.database.connect() as connection:
        count = connection.execute(select(func.count()).select_from(ChatTurn)).scalar_one()
        assert count == (0 if mode == "new_request" else 1)


def test_thread_detail_is_consistent_with_concurrent_owner_session(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A second session avoids serialization by the first session's row lock.
    with TestClient(
        create_app(),
        base_url="https://rag.test",
        backend_options={"loop_factory": create_event_loop},
    ) as second:
        csrf = CsrfResponse.model_validate_json(second.get("/api/auth/csrf").content).csrf_token
        login = second.post(
            "/api/auth/login",
            json={"login": "reader", "password": "Synthetic-Pass-123"},
            headers={"Origin": "https://rag.test", "X-CSRF-Token": csrf},
        )
        assert login.status_code == 200
        entered, release = Event(), Event()
        backend: list[int] = []

        async def paused(
            uow: security.ReadUoW, thread_id: UUID, *, lock: bool = False
        ) -> ChatThread:
            result = await get_owned_thread(uow, thread_id, lock=lock)
            backend.append(
                (
                    await uow.connection.execute(select(func.pg_backend_pid(type_=Integer)))
                ).scalar_one()
            )
            entered.set()
            assert await run_sync(release.wait, 10)
            return result

        monkeypatch.setattr(routes, "get_owned_thread", paused)
        payload = MessageRequest(
            request_id=uuid4(), expected_thread_revision=0, user_input="FIRST QUESTION"
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            reader = pool.submit(chat_case.client.get, f"/api/chat/threads/{chat_case.thread}")
            try:
                assert entered.wait(10)
                writer = pool.submit(
                    second.post,
                    f"/api/chat/threads/{chat_case.thread}/messages",
                    json=payload.model_dump(mode="json"),
                    headers=ChatHttp.csrf(second),
                )
                _ = wait_for_thread_lock(chat_case.database, backend[0], writer)
            finally:
                release.set()
            read = reader.result(10)
            assert writer.result(10).status_code == 200
        assert read.status_code == 200
        detail = ThreadDetail.model_validate_json(read.content)
        completed_turns = [turn for turn in detail.turns if turn.state != "pending"]
        assert detail.revision == len(completed_turns)
        assert (detail.title == "Новый диалог") == (not detail.turns)
