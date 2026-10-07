from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from threading import Barrier, Event
from typing import override
from uuid import uuid4

import pytest
from sqlalchemy import text

from rag_access_guard import TokenCounter
from rag_access_guard_api.adapters import embeddings, llm
from rag_access_guard_api.adapters.tokenizer import TokenizerUnavailableError, get_tokenizer
from rag_access_guard_api.schemas.chat import MessageRequest
from rag_access_guard_api.services import chat, query_validation
from tests.integration.embedding_fixtures import DeterministicEmbedder
from tests.support.chat import ChatHttp
from tests.support.chat_generation import ChatCase


def test_e5_overflow_is_rejected_before_pending_or_inference(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    query = " ".join(["test"] * 508)
    assert get_tokenizer().embedding_input_tokens(query, kind="query") == 513
    assert llm.FakeTokenCounter().count(query) == 508
    observed: list[str] = []

    class ObservedEmbedder(DeterministicEmbedder):
        @override
        async def embed_query(self, text: str) -> tuple[float, ...]:
            observed.append(text)
            return await super().embed_query(text)

    monkeypatch.setattr(embeddings, "get_embedding_adapter", ObservedEmbedder)
    response = chat_case.client.post(
        f"/api/chat/threads/{chat_case.thread}/messages",
        json={"request_id": str(uuid4()), "expected_thread_revision": 0, "user_input": query},
        headers=ChatHttp.csrf(chat_case.client),
    )
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "query_too_long"}}
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["vary"] == "Cookie"
    assert observed == []
    assert chat_case.model.call_count == 0
    detail = chat_case.read()
    assert detail.revision == 0
    assert detail.turns == ()
    with chat_case.database.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM chat_turns")).scalar_one() == 0


@pytest.mark.parametrize("disabled", [False, True])
def test_new_request_with_unavailable_counter_does_not_reserve(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch, *, disabled: bool
) -> None:
    def unavailable() -> TokenCounter | None:
        if disabled:
            return None
        raise llm.LLMUnavailableError

    monkeypatch.setattr(llm, "get_token_counter", unavailable)
    response = chat_case.client.post(
        f"/api/chat/threads/{chat_case.thread}/messages",
        json={"request_id": str(uuid4()), "expected_thread_revision": 0, "user_input": "QUESTION"},
        headers=ChatHttp.csrf(chat_case.client),
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "Service unavailable"}
    assert chat_case.model.call_count == 0
    detail = chat_case.read()
    assert detail.revision == 0
    assert detail.turns == ()


@pytest.mark.parametrize("denial", ["foreign", "expired", "csrf"])
def test_authorization_precedes_unavailable_model_preflight(
    chat_case: ChatCase,
    chat_http: ChatHttp,
    monkeypatch: pytest.MonkeyPatch,
    denial: str,
) -> None:
    def unavailable() -> TokenCounter:
        raise llm.LLMUnavailableError

    monkeypatch.setattr(llm, "get_token_counter", unavailable)
    client = chat_http.other if denial == "foreign" else chat_case.client
    headers = dict(ChatHttp.csrf(client))
    if denial == "expired":
        with chat_case.database.begin() as connection:
            _ = connection.execute(
                text("UPDATE sessions SET absolute_expires_at=clock_timestamp()")
            )
    if denial == "csrf":
        headers["X-CSRF-Token"] = "invalid"
    response = client.post(
        f"/api/chat/threads/{chat_case.thread}/messages",
        json={
            "request_id": str(uuid4()),
            "expected_thread_revision": 0,
            "user_input": " ".join(["test"] * 508),
        },
        headers=headers,
    )
    assert response.status_code == {"foreign": 404, "expired": 401, "csrf": 403}[denial]
    assert response.headers["cache-control"] == "private, no-store"
    assert chat_case.model.call_count == 0
    with chat_case.database.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM chat_turns")).scalar_one() == 0


def test_payload_conflict_precedes_new_query_validation(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="QUESTION")
    _ = chat_case.send(request)

    def unavailable() -> TokenCounter:
        raise llm.LLMUnavailableError

    monkeypatch.setattr(llm, "get_token_counter", unavailable)
    response = chat_case.client.post(
        f"/api/chat/threads/{chat_case.thread}/messages",
        json=request.model_copy(update={"user_input": " ".join(["test"] * 508)}).model_dump(
            mode="json"
        ),
        headers=ChatHttp.csrf(chat_case.client),
    )
    assert response.status_code == 409
    assert response.json() == {"detail": "request_conflict"}
    assert chat_case.model.call_count == 1


def test_unavailable_e5_counter_does_not_reserve(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable() -> None:
        raise TokenizerUnavailableError

    monkeypatch.setattr(query_validation, "get_tokenizer", unavailable)
    response = chat_case.client.post(
        f"/api/chat/threads/{chat_case.thread}/messages",
        json={"request_id": str(uuid4()), "expected_thread_revision": 0, "user_input": "QUESTION"},
        headers=ChatHttp.csrf(chat_case.client),
    )
    assert response.status_code == 503
    assert chat_case.read().turns == ()
    assert chat_case.read().revision == 0
    assert chat_case.model.call_count == 0


def test_two_validated_requests_can_reserve_only_one_pending(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    both_validated, allow_reservation = Event(), Event()
    arrived = Barrier(2, action=both_validated.set, timeout=10)
    original = query_validation.validate_query

    def paused(text: str, *, model_counter: TokenCounter | None) -> None:
        original(text, model_counter=model_counter)
        _ = arrived.wait()
        assert allow_reservation.wait(10)

    monkeypatch.setattr(chat, "validate_query", paused)
    chat_case.model.hold = True
    requests = [
        MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="QUESTION")
        for _ in range(2)
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = [
            pool.submit(
                chat_case.client.post,
                f"/api/chat/threads/{chat_case.thread}/messages",
                json=request.model_dump(mode="json"),
                headers=ChatHttp.csrf(chat_case.client),
            )
            for request in requests
        ]
        try:
            assert both_validated.wait(10)
            assert chat_case.read().turns == ()
            with chat_case.database.connect() as connection:
                assert connection.execute(text("SELECT count(*) FROM chat_turns")).scalar_one() == 0
            allow_reservation.set()
            assert chat_case.model.entered.wait(10)
            done, pending = wait(responses, timeout=10, return_when=FIRST_COMPLETED)
            assert len(done) == len(pending) == 1
            loser = next(iter(done)).result()
            assert loser.status_code == 409
            assert loser.json() == {"detail": "request_in_progress"}
            with chat_case.database.connect() as connection:
                states = connection.execute(text("SELECT state FROM chat_turns")).scalars().all()
            assert states == ["pending"]
        finally:
            allow_reservation.set()
            chat_case.model.resume.set()
        assert sorted(response.result(10).status_code for response in responses) == [200, 409]
    assert chat_case.model.call_count == 1
    detail = chat_case.read()
    assert detail.revision == 1
    assert len(detail.turns) == 1
    assert detail.turns[0].state == "available"
