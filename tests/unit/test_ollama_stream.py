from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import override

import anyio
import httpx2
import pytest

from rag_access_guard_api.adapters import ollama
from rag_access_guard_api.adapters.ollama import OllamaAdapter
from rag_access_guard_api.schemas.generation import GenerationUnavailable


@dataclass(frozen=True, slots=True)
class Body(httpx2.AsyncByteStream):
    slow: bool = False
    consumed: list[int] = field(default_factory=list)
    closed: list[bool] = field(default_factory=list)

    @override
    async def __aiter__(self) -> AsyncIterator[bytes]:
        if self.slow:
            await anyio.sleep_forever()
        for index in range(10):
            self.consumed.append(index)
            yield b" " * 65536

    @override
    async def aclose(self) -> None:
        self.closed.append(True)


@pytest.mark.anyio
async def test_oversized_body_is_stopped_before_full_accumulation() -> None:
    body = Body()

    def respond(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, stream=body)

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(respond), base_url="http://127.0.0.1:11434"
    ) as client:
        with pytest.raises(GenerationUnavailable):
            _ = await OllamaAdapter(client).generate(user_input="Q", system_supplied_context="C")
    assert body.consumed == [0, 1, 2]
    assert body.closed == [True]


@pytest.mark.anyio
async def test_stalled_response_body_obeys_whole_call_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = Body(slow=True)

    def respond(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, stream=body)

    monkeypatch.setattr(ollama, "TIMEOUT_SECONDS", 0.01)
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(respond), base_url="http://127.0.0.1:11434"
    ) as client:
        with pytest.raises(GenerationUnavailable):
            _ = await OllamaAdapter(client).generate(user_input="Q", system_supplied_context="C")
    assert body.consumed == []
    assert body.closed == [True]


@pytest.mark.anyio
async def test_compressed_payload_is_rejected_before_decompression() -> None:
    body = Body()

    def respond(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, stream=body, headers={"Content-Encoding": "gzip"})

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(respond), base_url="http://127.0.0.1:11434"
    ) as client:
        with pytest.raises(GenerationUnavailable):
            _ = await OllamaAdapter(client).generate(user_input="Q", system_supplied_context="C")
    assert body.consumed == []
    assert body.closed == [True]
