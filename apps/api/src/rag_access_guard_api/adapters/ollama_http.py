"""Finite local HTTP calls with no proxy, redirect, retry or compressed-body escape."""

import socket
from http import HTTPStatus
from typing import Final

import httpx2
from pydantic import JsonValue

from rag_access_guard_api.schemas.generation import GenerationUnavailable as LLMUnavailableError

MAX_RESPONSE_BYTES: Final = 131072
TIMEOUT_SECONDS: Final = 60


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
    client: httpx2.AsyncClient, path: str, *, payload: JsonValue = None
) -> bytes:
    """Bound the undecoded body before accumulating or parsing server data."""
    async with client.stream("GET" if payload is None else "POST", path, json=payload) as response:
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
