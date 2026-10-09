"""Finite local HTTP calls with no proxy, redirect, retry or compressed-body escape."""

import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass
from http import HTTPStatus
from typing import Final, override

import httpx2
from pydantic import JsonValue

from rag_access_guard_api.schemas.generation import GenerationUnavailable as LLMUnavailableError

MAX_RESPONSE_BYTES: Final = 131072
TIMEOUT_SECONDS: Final = 60


@dataclass(slots=True)
class RequestDispatch:
    """Mutable transport evidence: payload bytes handed off may have reached the engine."""

    possibly_sent: bool = False


class _TrackedBody(httpx2.AsyncByteStream):
    def __init__(self, source: httpx2.AsyncByteStream, dispatch: RequestDispatch) -> None:
        self._source: httpx2.AsyncByteStream = source
        self._dispatch: RequestDispatch = dispatch

    @override
    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self._source:
            if chunk:
                self._dispatch.possibly_sent = True
            yield chunk

    @override
    async def aclose(self) -> None:
        await self._source.aclose()


def create_client(base_url: str) -> httpx2.AsyncClient:
    """Own this client with an async context manager at the generation boundary."""
    limits = httpx2.Limits(max_connections=50, max_keepalive_connections=20, keepalive_expiry=30)
    transport = httpx2.AsyncHTTPTransport(
        http2=True,
        retries=0,
        limits=limits,
        trust_env=False,
        socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
    )
    return httpx2.AsyncClient(
        transport=transport,
        base_url=base_url,
        trust_env=False,
        follow_redirects=False,
        timeout=httpx2.Timeout(connect=5, read=60, write=10, pool=10),
        headers={"Accept-Encoding": "identity"},
    )


async def bounded_request(
    client: httpx2.AsyncClient,
    path: str,
    *,
    payload: JsonValue = None,
    dispatch: RequestDispatch | None = None,
) -> bytes:
    """Bound the undecoded body before accumulating or parsing server data."""
    request = client.build_request("GET" if payload is None else "POST", path, json=payload)
    if dispatch is not None:
        match request.stream:
            case httpx2.AsyncByteStream() as stream:
                request.stream = _TrackedBody(stream, dispatch)
            case _:
                raise LLMUnavailableError
    response = await client.send(request, stream=True)
    try:
        if (
            response.status_code != HTTPStatus.OK
            or response.headers.get("content-encoding", "identity") != "identity"
        ):
            raise LLMUnavailableError
        raw = bytearray()
        async for part in response.aiter_bytes():
            if len(raw) + len(part) > MAX_RESPONSE_BYTES:
                raise LLMUnavailableError
            raw.extend(part)
        return bytes(raw)
    finally:
        await response.aclose()
