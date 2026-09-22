import base64
import hashlib
import logging

import pytest
import uvicorn
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from rag_access_guard_api import server
from rag_access_guard_api.main import create_app
from rag_access_guard_api.persistence import Session
from rag_access_guard_api.schemas.auth import CsrfResponse
from rag_access_guard_api.server import create_event_loop
from rag_access_guard_api.services import passwords


def test_tokens_have_32_bytes_entropy_and_only_digest_is_stored(
    authenticated_client: TestClient, auth_database: Engine
) -> None:
    session = authenticated_client.cookies["__Host-rag_session"]
    csrf = authenticated_client.cookies["__Host-rag_csrf"]
    assert session != csrf
    assert len(base64.urlsafe_b64decode(session + "=")) == 32
    assert len(base64.urlsafe_b64decode(csrf + "=")) == 32
    with auth_database.connect() as connection:
        session_digest, csrf_digest = (
            connection.execute(select(Session.token_digest, Session.csrf_token_digest))
            .tuples()
            .one()
        )
    assert session_digest == hashlib.sha256(base64.urlsafe_b64decode(session + "=")).digest()
    assert csrf_digest == hashlib.sha256(base64.urlsafe_b64decode(csrf + "=")).digest()


@pytest.mark.parametrize("password", ["x" * 1025, "я" * 513])
def test_password_byte_limit_rejects_before_hash(auth_client: TestClient, password: str) -> None:
    response = auth_client.post(
        "/api/auth/login",
        json={"login": "reader", "password": password},
        headers={"Origin": "https://rag.test"},
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid request"}
    assert password not in response.text


def test_auth_logs_contain_no_password_or_token(
    auth_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    csrf = CsrfResponse.model_validate_json(auth_client.get("/api/auth/csrf").content).csrf_token
    preauth = auth_client.cookies["__Host-rag_preauth"]
    assert (
        auth_client.post(
            "/api/auth/login",
            json={"login": "reader", "password": "Synthetic-Pass-123"},
            headers={"Origin": "https://rag.test", "X-CSRF-Token": csrf},
        ).status_code
        == 200
    )
    for value in ("Synthetic-Pass-123", csrf, preauth, auth_client.cookies["__Host-rag_session"]):
        assert value not in caplog.text


def test_authenticated_login_conflicts(authenticated_client: TestClient) -> None:
    response = authenticated_client.post(
        "/api/auth/login",
        json={"login": "reader", "password": "Synthetic-Pass-123"},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": authenticated_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 409


def test_login_forbids_unknown_fields_and_non_json(auth_client: TestClient) -> None:
    response = auth_client.post(
        "/api/auth/login",
        json={"login": "reader", "password": "Synthetic-Pass-123", "is_admin": True},
        headers={"Origin": "https://rag.test"},
    )
    assert response.status_code == 422
    response = auth_client.post(
        "/api/auth/login",
        content='{"login":"reader","password":"Synthetic-Pass-123"}',
        headers={"Origin": "https://rag.test", "Content-Type": "text/plain"},
    )
    assert response.status_code in {415, 422}


def test_loopback_cookie_mode_is_explicit(
    auth_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert auth_client.base_url.host == "rag.test"
    monkeypatch.setenv("RAG_ACCESS_GUARD_LOOPBACK_DEVELOPMENT", "true")
    monkeypatch.setenv("RAG_ACCESS_GUARD_AUTH_ORIGIN", "http://127.0.0.1:5173")
    with TestClient(
        create_app(),
        base_url="http://127.0.0.1:5173",
        backend_options={"loop_factory": create_event_loop},
    ) as local:
        response = local.get("/api/auth/csrf")
        assert response.status_code == 200
        assert "rag_preauth_local" in local.cookies
        for header in response.headers.get_list("set-cookie"):
            assert "Secure" not in header
            assert "Domain=" not in header


def test_server_disables_proxy_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []

    def run(application: str, *, host: str, port: int, loop: str, proxy_headers: bool) -> None:
        assert application == server.APPLICATION_IMPORT
        assert host == "127.0.0.1"
        assert port == 8000
        assert loop == server.EVENT_LOOP_IMPORT
        assert proxy_headers is False
        called.append(True)

    monkeypatch.setattr(uvicorn, "run", run)
    server.run()
    assert called == [True]


def test_unexpected_auth_failure_is_sanitized_and_not_cached(
    auth_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert auth_client.base_url.host == "rag.test"

    def failing_verify(_password: str, _encoded: str) -> bool:
        message = "Synthetic internal failure"
        raise RuntimeError(message)

    monkeypatch.setattr(passwords, "verify_password", failing_verify)
    with TestClient(
        create_app(),
        base_url="https://rag.test",
        backend_options={"loop_factory": create_event_loop},
        raise_server_exceptions=False,
    ) as client:
        csrf = CsrfResponse.model_validate_json(client.get("/api/auth/csrf").content).csrf_token
        response = client.post(
            "/api/auth/login",
            json={"login": "reader", "password": "Synthetic-Pass-123"},
            headers={"Origin": "https://rag.test", "X-CSRF-Token": csrf},
        )
    assert response.status_code == 500
    assert response.headers.get("cache-control") == "private, no-store"
    assert response.headers.get("vary") == "Cookie"
    assert response.json() == {"detail": "Internal server error"}
