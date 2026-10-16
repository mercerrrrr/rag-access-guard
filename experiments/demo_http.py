"""Ordinary actor sessions over the dedicated loopback API, without retries."""

import socket

import httpx2

from rag_access_guard_api.demo.contracts import DemoConfig
from rag_access_guard_api.demo.credentials import CredentialEntry, CredentialJournal
from rag_access_guard_api.demo.evaluation import DemoDatasetError
from rag_access_guard_api.schemas.auth import CsrfResponse, LoginRequest, LoginResponse


def credential_for(journal: CredentialJournal, login: str) -> CredentialEntry:
    """Resolve only a journal-bound actor; missing credentials never become another account."""
    entry = next((e for e in journal.entries if e.login == login), None)
    if entry is None:
        raise DemoDatasetError(code="credential_actor_absent")
    return entry


def demo_client(config: DemoConfig) -> httpx2.AsyncClient:
    """Fixed owned origins; retry/redirect forwarding is forbidden for measured requests."""
    limits = httpx2.Limits(max_connections=20, max_keepalive_connections=5, keepalive_expiry=30)
    transport = httpx2.AsyncHTTPTransport(
        http2=True,
        retries=0,
        limits=limits,
        socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
    )
    return httpx2.AsyncClient(
        base_url=f"http://127.0.0.1:{config.runtime.api_port}",
        transport=transport,
        timeout=httpx2.Timeout(connect=5, read=150, write=10, pool=10),
        follow_redirects=False,
        trust_env=False,
        headers={"Origin": f"http://127.0.0.1:{config.runtime.web_port}"},
    )


async def login_actor(client: httpx2.AsyncClient, entry: CredentialEntry) -> str:
    """Use preauth challenge, CSRF and server session cookies, never an actor selector."""
    challenge = await client.get("/api/auth/csrf")
    _ = challenge.raise_for_status()
    csrf = CsrfResponse.model_validate_json(challenge.content)
    request = LoginRequest(login=entry.login, password=entry.password.get_secret_value())
    response = await client.post(
        "/api/auth/login",
        content=request.model_dump_json(),
        headers={"Content-Type": "application/json", "X-CSRF-Token": csrf.csrf_token},
    )
    _ = response.raise_for_status()
    account = LoginResponse.model_validate_json(response.content)
    if account.user.login != entry.login:
        raise DemoDatasetError(code="login_identity_mismatch")
    client.headers["X-CSRF-Token"] = account.csrf_token
    session = client.cookies.get("rag_session_local")
    if session is None:
        raise DemoDatasetError(code="local_session_missing")
    return session
