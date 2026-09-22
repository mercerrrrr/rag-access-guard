import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text

from rag_access_guard_api.persistence import AuthChallenge
from rag_access_guard_api.schemas.auth import CsrfResponse
from rag_access_guard_api.services.tokens import issue_token


def test_login_requires_pre_auth_csrf(auth_client: TestClient) -> None:
    # Given an existing account and no pre-auth challenge.
    # When credentials arrive without the synchronizer header.
    response = auth_client.post(
        "/api/auth/login",
        json={"login": "reader", "password": "Synthetic-Pass-123"},
        headers={"Origin": "https://rag.test"},
    )
    # Then login is forbidden even for correct credentials.
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


def test_pre_auth_challenge_is_single_use(auth_client: TestClient) -> None:
    csrf = CsrfResponse.model_validate_json(auth_client.get("/api/auth/csrf").content).csrf_token
    headers = {"Origin": "https://rag.test", "X-CSRF-Token": csrf}
    assert (
        auth_client.post(
            "/api/auth/login",
            json={"login": "reader", "password": "wrong-password"},
            headers=headers,
        ).status_code
        == 401
    )
    assert (
        auth_client.post(
            "/api/auth/login",
            json={"login": "reader", "password": "Synthetic-Pass-123"},
            headers=headers,
        ).status_code
        == 403
    )


def test_challenge_expires_at_ten_minutes(auth_client: TestClient, auth_database: Engine) -> None:
    csrf = CsrfResponse.model_validate_json(auth_client.get("/api/auth/csrf").content).csrf_token
    with auth_database.begin() as connection:
        created, expires = (
            connection.execute(select(AuthChallenge.created_at, AuthChallenge.expires_at))
            .tuples()
            .one()
        )
        assert (expires - created).total_seconds() == 600
        _ = connection.execute(
            text(
                """UPDATE auth_challenges SET created_at=created_at-interval '11 minutes',
                expires_at=expires_at-interval '11 minutes'"""
            )
        )
    response = auth_client.post(
        "/api/auth/login",
        json={"login": "reader", "password": "Synthetic-Pass-123"},
        headers={"Origin": "https://rag.test", "X-CSRF-Token": csrf},
    )
    assert response.status_code == 403


def test_restore_preserves_csrf_across_two_tabs(authenticated_client: TestClient) -> None:
    existing = authenticated_client.cookies["__Host-rag_csrf"]
    for _ in range(2):
        assert authenticated_client.get("/api/auth/me").status_code == 200
        restored = CsrfResponse.model_validate_json(
            authenticated_client.get("/api/auth/csrf").content
        )
        assert restored.csrf_token == existing


def test_missing_csrf_cookie_resynchronizes(authenticated_client: TestClient) -> None:
    old = authenticated_client.cookies["__Host-rag_csrf"]
    authenticated_client.cookies.delete("__Host-rag_csrf")
    new = CsrfResponse.model_validate_json(
        authenticated_client.get("/api/auth/csrf").content
    ).csrf_token
    assert new != old
    assert (
        authenticated_client.post(
            "/api/auth/logout", headers={"Origin": "https://rag.test", "X-CSRF-Token": old}
        ).status_code
        == 403
    )
    assert (
        authenticated_client.post(
            "/api/auth/logout", headers={"Origin": "https://rag.test", "X-CSRF-Token": new}
        ).status_code
        == 204
    )


@pytest.mark.parametrize("origin", [None, "null", "https://foreign.test", "https://rag.test.evil"])
def test_foreign_null_missing_origin_is_rejected(
    authenticated_client: TestClient, origin: str | None
) -> None:
    headers = {"X-CSRF-Token": authenticated_client.cookies["__Host-rag_csrf"]}
    if origin is not None:
        headers["Origin"] = origin
    response = authenticated_client.post("/api/auth/logout", headers=headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


@pytest.mark.parametrize("origin", ["null", "https://foreign.test"])
def test_get_explicit_foreign_origin_is_rejected(auth_client: TestClient, origin: str) -> None:
    assert auth_client.get("/api/auth/csrf", headers={"Origin": origin}).status_code == 403


@pytest.mark.parametrize("token", ["", "forged", "a" * 43, "A" * 42 + "B", issue_token()])
def test_forged_session_cookie_never_authenticates(auth_client: TestClient, token: str) -> None:
    auth_client.cookies.set("__Host-rag_session", token)
    assert auth_client.get("/api/auth/me").status_code == 401


def test_secure_host_cookie_attributes(auth_client: TestClient) -> None:
    response = auth_client.get("/api/auth/csrf")
    for header in response.headers.get_list("set-cookie"):
        assert "Secure" in header
        assert "SameSite=lax" in header
        assert "Path=/" in header
        assert "Domain=" not in header
        assert ("HttpOnly" in header) is ("__Host-rag_preauth=" in header)
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["vary"] == "Cookie"


def test_csrf_header_from_another_session_is_rejected(authenticated_client: TestClient) -> None:
    first_session = authenticated_client.cookies["__Host-rag_session"]
    first_csrf = authenticated_client.cookies["__Host-rag_csrf"]
    authenticated_client.cookies.clear()
    csrf = CsrfResponse.model_validate_json(
        authenticated_client.get("/api/auth/csrf").content
    ).csrf_token
    assert (
        authenticated_client.post(
            "/api/auth/login",
            json={"login": "reader", "password": "Synthetic-Pass-123"},
            headers={"Origin": "https://rag.test", "X-CSRF-Token": csrf},
        ).status_code
        == 200
    )
    assert authenticated_client.cookies["__Host-rag_session"] != first_session
    assert (
        authenticated_client.post(
            "/api/auth/logout", headers={"Origin": "https://rag.test", "X-CSRF-Token": first_csrf}
        ).status_code
        == 403
    )
