from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import Literal
from uuid import uuid4

import pytest
from sqlalchemy import text

from rag_access_guard_api.adapters import embeddings
from rag_access_guard_api.adapters.embeddings import E5EmbeddingAdapter, get_embedding_adapter
from rag_access_guard_api.adapters.inference_runtime import InferenceBusyError, InferenceRuntime
from rag_access_guard_api.schemas.auth import CsrfResponse
from rag_access_guard_api.schemas.chat import MessageRequest, ThreadView
from rag_access_guard_api.services.passwords import hash_password
from tests.support.chat import ChatHttp
from tests.support.chat_generation import ChatCase


def test_other_principal_in_same_app_cannot_start_second_generation(chat_case: ChatCase) -> None:
    with chat_case.database.begin() as connection:
        _ = connection.execute(
            text("""INSERT INTO users(id,login,display_name,password_hash)
            VALUES(:id,'second','Second',:hash)"""),
            {"id": uuid4(), "hash": hash_password("Synthetic-Other-123")},
        )
    original_cookies = dict(chat_case.client.cookies)
    chat_case.model.hold = True
    payload = MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="FIRST")
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(chat_case.send, payload)
        try:
            assert chat_case.model.entered.wait(10)
            chat_case.client.cookies.clear()
            csrf = CsrfResponse.model_validate_json(
                chat_case.client.get("/api/auth/csrf").content
            ).csrf_token
            assert (
                chat_case.client.post(
                    "/api/auth/login",
                    json={"login": "second", "password": "Synthetic-Other-123"},
                    headers={"Origin": "https://rag.test", "X-CSRF-Token": csrf},
                ).status_code
                == 200
            )
            other = ThreadView.model_validate_json(
                chat_case.client.post(
                    "/api/chat/threads", json={}, headers=ChatHttp.csrf(chat_case.client)
                ).content
            )
            response = chat_case.client.post(
                f"/api/chat/threads/{other.id}/messages",
                json=payload.model_copy(update={"request_id": uuid4()}).model_dump(mode="json"),
                headers=ChatHttp.csrf(chat_case.client),
            )
            assert response.status_code == 429
            assert response.json() == {"detail": {"code": "inference_busy"}}
            with chat_case.database.connect() as connection:
                assert (
                    connection.execute(
                        text("SELECT count(*) FROM chat_turns WHERE thread_id=:id"),
                        {"id": other.id},
                    ).scalar_one()
                    == 0
                )
        finally:
            chat_case.client.cookies.clear()
            chat_case.client.cookies.update(original_cookies)
            chat_case.model.resume.set()
        assert first.result(10).turn.state == "available"
    assert chat_case.model.call_count == 1


def test_query_and_upload_share_app_physical_capacity(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = Event(), Event()
    owners: list[InferenceRuntime] = []

    def factory() -> E5EmbeddingAdapter:
        adapter = get_embedding_adapter()
        assert isinstance(adapter, E5EmbeddingAdapter)
        owners.append(adapter.runtime)
        return adapter

    def encode(
        _self: E5EmbeddingAdapter, texts: tuple[str, ...], _kind: Literal["query", "passage"]
    ) -> tuple[tuple[float, ...], ...]:
        entered.set()
        if not release.wait(10):
            raise TimeoutError
        return tuple((1.0, *(0.0 for _ in range(383))) for _ in texts)

    monkeypatch.setattr(embeddings, "get_embedding_adapter", factory)
    monkeypatch.setattr(E5EmbeddingAdapter, "_encode", encode)
    with ThreadPoolExecutor(max_workers=1) as pool:
        query = pool.submit(
            chat_case.client.post,
            "/api/search",
            json={"query": "CONTROLLED"},
            headers=ChatHttp.csrf(chat_case.client),
        )
        try:
            assert entered.wait(10)
            upload = chat_case.client.post(
                "/api/admin/documents",
                data={"title": "Controlled"},
                files={"file": ("controlled.txt", b"CONTROLLED", "text/plain")},
                headers=ChatHttp.csrf(chat_case.client),
            )
            assert upload.status_code == 429
            assert upload.json() == {"detail": {"code": "inference_busy"}}
            assert owners[0] is owners[1]
            assert owners[0].accepted_embedding_jobs == 1
        finally:
            release.set()
        assert query.result(10).status_code == 200


def test_embedding_busy_after_reserve_completes_neutral(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BusyEmbedder:
        async def embed_query(self, _text: str) -> tuple[float, ...]:
            raise InferenceBusyError

    monkeypatch.setattr(embeddings, "get_embedding_adapter", BusyEmbedder)
    response = chat_case.send(
        MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="CONTROLLED")
    )
    assert response.turn.state == "neutral"
    assert response.thread_revision == 1
    assert chat_case.model.call_count == 0


def test_other_chat_is_busy_without_pending_or_revision_change(chat_case: ChatCase) -> None:
    created = chat_case.client.post(
        "/api/chat/threads", json={}, headers=ChatHttp.csrf(chat_case.client)
    )
    other = ThreadView.model_validate_json(created.content)
    chat_case.model.hold = True
    payload = MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="QUESTION")
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(chat_case.send, payload)
        try:
            assert chat_case.model.entered.wait(10)
            second = chat_case.client.post(
                f"/api/chat/threads/{other.id}/messages",
                json={
                    "request_id": str(uuid4()),
                    "expected_thread_revision": 0,
                    "user_input": "SECOND",
                },
                headers=ChatHttp.csrf(chat_case.client),
            )
            assert second.status_code == 429
            assert second.json() == {"detail": {"code": "inference_busy"}}
            assert second.headers["retry-after"] == "2"
            assert second.headers["cache-control"] == "private, no-store"
            with chat_case.database.connect() as connection:
                assert (
                    connection.execute(
                        text("SELECT count(*) FROM chat_turns WHERE thread_id=:id"),
                        {"id": other.id},
                    ).scalar_one()
                    == 0
                )
                assert (
                    connection.execute(
                        text("SELECT revision FROM chat_threads WHERE id=:id"), {"id": other.id}
                    ).scalar_one()
                    == 0
                )
        finally:
            chat_case.model.resume.set()
        assert first.result(10).turn.state == "available"
    assert chat_case.model.call_count == 1


def test_completed_replay_works_during_other_chat_generation(chat_case: ChatCase) -> None:
    completed = MessageRequest(
        request_id=uuid4(), expected_thread_revision=0, user_input="QUESTION"
    )
    original = chat_case.send(completed)
    created = chat_case.client.post(
        "/api/chat/threads", json={}, headers=ChatHttp.csrf(chat_case.client)
    )
    other = ThreadView.model_validate_json(created.content)
    chat_case.model.hold = True
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            chat_case.client.post,
            f"/api/chat/threads/{other.id}/messages",
            json={
                "request_id": str(uuid4()),
                "expected_thread_revision": 0,
                "user_input": "SECOND",
            },
            headers=ChatHttp.csrf(chat_case.client),
        )
        try:
            assert chat_case.model.entered.wait(10)
            replay = chat_case.send(completed)
            assert replay.replayed
            assert replay.turn == original.turn
            conflict = chat_case.client.post(
                f"/api/chat/threads/{chat_case.thread}/messages",
                json=completed.model_copy(update={"user_input": "CHANGED"}).model_dump(mode="json"),
                headers=ChatHttp.csrf(chat_case.client),
            )
            assert conflict.status_code == 409
        finally:
            chat_case.model.resume.set()
        assert pending.result(10).status_code == 200
    assert chat_case.model.call_count == 2
