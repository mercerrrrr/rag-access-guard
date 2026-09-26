from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Event
from uuid import uuid4

import anyio
import httpx2
import pytest
from anyio.to_thread import run_sync
from pydantic import JsonValue, TypeAdapter
from sqlalchemy import text

from rag_access_guard_api.adapters import llm
from rag_access_guard_api.adapters.model_tokens import get_model_counter
from rag_access_guard_api.adapters.ollama import OllamaAdapter
from rag_access_guard_api.schemas.chat import MessageRequest
from tests.support.chat import ChatHttp
from tests.support.chat_generation import ChatCase


@dataclass(frozen=True, slots=True)
class Wire:
    entered: Event = field(default_factory=Event)
    resume: Event = field(default_factory=Event)
    hold: Event = field(default_factory=Event)
    broken: Event = field(default_factory=Event)
    requests: list[JsonValue] = field(default_factory=list)


@pytest.fixture
def ollama_wire(chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch) -> Iterator[Wire]:
    del chat_case
    wire = Wire()

    async def respond(request: httpx2.Request) -> httpx2.Response:
        wire.requests.append(TypeAdapter[JsonValue](JsonValue).validate_json(request.content))
        wire.entered.set()
        if wire.hold.is_set() and not await run_sync(wire.resume.wait, 20):
            raise TimeoutError
        if wire.broken.is_set():
            return httpx2.Response(200, content=b'{"PRIVATE_PARTIAL":')
        return httpx2.Response(
            200,
            json={
                "done": True,
                "done_reason": "stop",
                "context": [42],
                "message": {
                    "role": "assistant",
                    "content": "LOCAL_FINAL_ANSWER",
                    "thinking": "PRIVATE_REASONING",
                },
            },
        )

    client = httpx2.AsyncClient(
        transport=httpx2.MockTransport(respond), base_url="http://127.0.0.1:11434"
    )
    adapter = OllamaAdapter(client)
    monkeypatch.setattr(llm, "get_llm_adapter", lambda: adapter)
    monkeypatch.setattr(llm, "get_token_counter", get_model_counter)
    try:
        yield wire
    finally:
        anyio.run(client.aclose)


def question() -> MessageRequest:
    return MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="QUESTION")


def test_local_answer_is_released_with_server_sources_and_no_reasoning(
    chat_case: ChatCase,
    ollama_wire: Wire,
    caplog: pytest.LogCaptureFixture,
) -> None:
    result = chat_case.send(question())
    assert result.turn.state == "available"
    assert result.turn.answer == "LOCAL_FINAL_ANSWER"
    assert len(result.turn.sources) == 1
    assert result.turn.sources[0].document_id == chat_case.document.id
    assert "PRIVATE_REASONING" not in result.model_dump_json()
    assert "PRIVATE_REASONING" not in caplog.text
    assert len(ollama_wire.requests) == 1
    with chat_case.database.connect() as connection:
        assert (
            connection.execute(text("SELECT answer FROM chat_turns")).scalar_one()
            == "LOCAL_FINAL_ANSWER"
        )


def test_local_transport_is_not_called_without_allowed_context(
    chat_case: ChatCase, ollama_wire: Wire
) -> None:
    chat_case.revoke()
    result = chat_case.send(question())
    assert result.turn.state == "neutral"
    assert ollama_wire.requests == []


def test_revoke_during_http_inference_blocks_storage_and_release(
    chat_case: ChatCase,
    ollama_wire: Wire,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ollama_wire.hold.set()
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(chat_case.send, question())
        try:
            assert ollama_wire.entered.wait(10)
            chat_case.revoke()
        finally:
            ollama_wire.resume.set()
        result = running.result(10)
    assert result.turn.state == "neutral"
    assert "LOCAL_FINAL_ANSWER" not in result.model_dump_json()
    assert "LOCAL_FINAL_ANSWER" not in caplog.text
    with chat_case.database.connect() as connection:
        assert connection.execute(text("SELECT answer FROM chat_turns")).scalar_one() is None
        assert connection.execute(text("SELECT count(*) FROM turn_sources")).scalar_one() == 0


def test_broken_ollama_output_is_not_stored_or_exposed(
    chat_case: ChatCase, ollama_wire: Wire, caplog: pytest.LogCaptureFixture
) -> None:
    ollama_wire.broken.set()
    result = chat_case.send(question())
    assert result.turn.state == "neutral"
    assert result.turn.answer is None
    assert result.turn.sources == ()
    assert "PRIVATE_PARTIAL" not in result.model_dump_json()
    assert "PRIVATE_PARTIAL" not in caplog.text
    with chat_case.database.connect() as connection:
        assert connection.execute(text("SELECT answer FROM chat_turns")).scalar_one() is None


def test_real_question_budget_rejects_before_reservation(
    chat_case: ChatCase, ollama_wire: Wire
) -> None:
    response = chat_case.client.post(
        f"/api/chat/threads/{chat_case.thread}/messages",
        json={
            "request_id": str(uuid4()),
            "expected_thread_revision": 0,
            "user_input": "Привет " * 600,
        },
        headers=ChatHttp.csrf(chat_case.client),
    )
    assert response.status_code == 422
    assert chat_case.read().turns == ()
    assert ollama_wire.requests == []


def test_second_turn_receives_only_explicit_verified_history(
    chat_case: ChatCase, ollama_wire: Wire
) -> None:
    _ = chat_case.send(question())
    result = chat_case.send(
        MessageRequest(request_id=uuid4(), expected_thread_revision=1, user_input="SECOND")
    )
    assert result.turn.state == "available"
    second = TypeAdapter[JsonValue](JsonValue).dump_json(ollama_wire.requests[1]).decode()
    assert "LOCAL_FINAL_ANSWER" in second
    assert "QUESTION" in second
    assert "PRIVATE_REASONING" not in second
    assert '"context":' not in second
    assert "PROTECTED_SYNTHETIC" in second
