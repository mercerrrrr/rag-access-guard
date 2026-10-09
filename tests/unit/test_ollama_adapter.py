from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Final

import anyio
import httpx2
import pytest
from pydantic import JsonValue, TypeAdapter

from rag_access_guard_api.adapters import ollama
from rag_access_guard_api.adapters.llm import LLMUnavailableError
from rag_access_guard_api.adapters.ollama import OllamaAdapter
from rag_access_guard_api.services.model_manifest import ModelManifest

JSON: Final = TypeAdapter[JsonValue](JsonValue)


@dataclass(frozen=True, slots=True)
class OllamaTransport:
    adapter: OllamaAdapter
    requests: list[JsonValue] = field(default_factory=list)
    replies: list[JsonValue] = field(default_factory=list)
    raw_replies: list[httpx2.Response] = field(default_factory=list)

    def reply(self, value: JsonValue) -> None:
        self.replies.append(value)


@pytest.fixture
async def ollama_transport() -> AsyncIterator[OllamaTransport]:
    def respond(request: httpx2.Request) -> httpx2.Response:
        fixture.requests.append(JSON.validate_json(request.content))
        if fixture.raw_replies:
            return fixture.raw_replies.pop(0)
        return httpx2.Response(200, json=fixture.replies.pop(0))

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(respond), base_url="http://127.0.0.1:11434"
    ) as client:
        fixture = OllamaTransport(OllamaAdapter(client))
        yield fixture


@pytest.mark.anyio
async def test_ollama_sends_explicit_messages_without_hidden_history(
    ollama_transport: OllamaTransport,
) -> None:
    ollama_transport.reply(
        {
            "message": {
                "role": "assistant",
                "content": "Разрешённый ответ",
                "thinking": "PRIVATE_REASONING",
            },
            "done": True,
        }
    )
    answer = await ollama_transport.adapter.generate(
        user_input="Вопрос", system_supplied_context="Разрешённый chunk"
    )
    assert answer == "Разрешённый ответ"
    assert len(ollama_transport.requests) == 1
    body = ollama_transport.requests[0]
    assert isinstance(body, dict)
    assert body["stream"] is False
    assert "tools" not in body
    assert "context" not in body
    messages = body["messages"]
    assert isinstance(messages, list)
    assert len(messages) == 3
    assert messages[1] == {"role": "user", "content": "system_supplied_context:\nРазрешённый chunk"}  # noqa: RUF001
    assert messages[2] == {"role": "user", "content": "user_input:\nВопрос"}  # noqa: RUF001


@pytest.mark.anyio
@pytest.mark.parametrize(
    "reply",
    [
        {"done": False, "message": {"role": "assistant", "content": "PARTIAL"}},
        {"done": True, "message": {"role": "user", "content": "WRONG_ROLE"}},
        {"done": True, "message": {"role": "assistant", "content": ""}},
        {"done": True, "message": {"role": "assistant", "content": "  "}},
        {"done": True, "message": {"role": "assistant", "content": "\u0000"}},
        {"done": True, "message": {"role": "assistant", "content": ["WRONG_TYPE"]}},
        {"done": True, "message": {"role": "assistant", "thinking": "THINKING_ONLY"}},
        {
            "done": True,
            "message": {
                "role": "assistant",
                "content": "TOOL",
                "tool_calls": [{"function": {"name": "secret"}}],
            },
        },
        {
            "done": True,
            "done_reason": "length",
            "message": {"role": "assistant", "content": "PARTIAL"},
        },
        {"done": True, "message": {"role": "assistant", "content": "x" * 65537}},
    ],
)
async def test_incomplete_or_unsupported_output_is_rejected(
    ollama_transport: OllamaTransport, reply: JsonValue
) -> None:
    ollama_transport.reply(reply)
    with pytest.raises(LLMUnavailableError) as raised:
        _ = await ollama_transport.adapter.generate(user_input="Q", system_supplied_context="C")
    assert str(raised.value) == ""


@pytest.mark.anyio
@pytest.mark.parametrize("status", [301, 400, 401, 404, 429, 500, 503])
async def test_http_error_never_becomes_an_answer(
    ollama_transport: OllamaTransport, status: int
) -> None:
    ollama_transport.raw_replies.append(
        httpx2.Response(
            status,
            json={"done": True, "message": {"role": "assistant", "content": "RAW_ERROR_SECRET"}},
        )
    )
    with pytest.raises(LLMUnavailableError):
        _ = await ollama_transport.adapter.generate(user_input="Q", system_supplied_context="C")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "raw",
    [b'{"message":', b"not json", b"[]", b" " * 131073],
    ids=["truncated", "malformed", "array", "oversized"],
)
async def test_malformed_and_oversized_body_is_rejected(
    ollama_transport: OllamaTransport, raw: bytes
) -> None:
    ollama_transport.raw_replies.append(httpx2.Response(200, content=raw))
    with pytest.raises(LLMUnavailableError):
        _ = await ollama_transport.adapter.generate(user_input="Q", system_supplied_context="C")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("question", "context"),
    [("word " * 1100, "C"), ("Q", "word " * 5100), ("я" * 8193, "C")],
    ids=["question-tokens", "context-tokens", "question-bytes"],
)
async def test_budget_overflow_never_reaches_transport(
    ollama_transport: OllamaTransport, question: str, context: str
) -> None:
    ollama_transport.reply({"done": True, "message": {"role": "assistant", "content": "NO"}})
    with pytest.raises(LLMUnavailableError):
        _ = await ollama_transport.adapter.generate(
            user_input=question, system_supplied_context=context
        )
    assert ollama_transport.requests == []


@pytest.mark.anyio
async def test_second_request_does_not_reuse_model_metadata(
    ollama_transport: OllamaTransport,
) -> None:
    ollama_transport.reply(
        {
            "done": True,
            "context": [42],
            "message": {
                "role": "assistant",
                "content": "FIRST_SECRET",
                "thinking": "PRIVATE_THINKING",
            },
        }
    )
    ollama_transport.reply({"done": True, "message": {"role": "assistant", "content": "SECOND"}})
    _ = await ollama_transport.adapter.generate(
        user_input="FIRST_QUESTION", system_supplied_context="FIRST_SOURCE"
    )
    assert (
        await ollama_transport.adapter.generate(
            user_input="SECOND_QUESTION", system_supplied_context="SECOND_SOURCE"
        )
        == "SECOND"
    )
    request = JSON.dump_json(ollama_transport.requests[1]).decode()
    assert "FIRST_" not in request
    assert "PRIVATE_THINKING" not in request
    assert '"context"' not in request


@pytest.mark.anyio
async def test_full_template_and_output_reserve_are_budgeted() -> None:
    calls: list[str] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.url.path)
        return httpx2.Response(
            200, json={"done": True, "message": {"role": "assistant", "content": "NO"}}
        )

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(respond), base_url="http://127.0.0.1:11434"
    ) as client:
        adapter = OllamaAdapter(
            client, manifest=ModelManifest(context_window=2048, max_output_tokens=1024)
        )
        with pytest.raises(LLMUnavailableError):
            _ = await adapter.generate(user_input="Q", system_supplied_context="word " * 1000)
    assert calls == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error_type", [httpx2.ConnectError, httpx2.ReadTimeout, httpx2.RemoteProtocolError]
)
async def test_transport_errors_are_neutral_without_retries(
    error_type: type[httpx2.RequestError],
) -> None:
    calls: list[str] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.url.path)
        message = "PRIVATE_HTTP_ERROR"
        raise error_type(message)

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(respond), base_url="http://127.0.0.1:11434"
    ) as client:
        with pytest.raises(LLMUnavailableError) as raised:
            _ = await OllamaAdapter(client).generate(user_input="Q", system_supplied_context="C")
    assert str(raised.value) == ""
    assert calls == ["/api/chat"]


@pytest.mark.anyio
async def test_whole_call_timeout_cancels_http_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    async def respond(_request: httpx2.Request) -> httpx2.Response:
        await anyio.sleep_forever()
        raise AssertionError

    monkeypatch.setattr(ollama, "TIMEOUT_SECONDS", 0.01)
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(respond), base_url="http://127.0.0.1:11434"
    ) as client:
        with pytest.raises(LLMUnavailableError):
            _ = await OllamaAdapter(client).generate(user_input="Q", system_supplied_context="C")
