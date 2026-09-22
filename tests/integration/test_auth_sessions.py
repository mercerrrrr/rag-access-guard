from fastapi.testclient import TestClient

from rag_access_guard_api.schemas.auth import CsrfResponse, LoginResponse


def test_login_rotates_session_and_csrf(auth_client: TestClient) -> None:
    bootstrap = auth_client.get("/api/auth/csrf")
    assert bootstrap.status_code == 200
    csrf = CsrfResponse.model_validate_json(bootstrap.content).csrf_token
    preauth = auth_client.cookies["__Host-rag_preauth"]
    response = auth_client.post(
        "/api/auth/login",
        json={"login": " READER ", "password": "Synthetic-Pass-123"},
        headers={"Origin": "https://rag.test", "X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    assert response.json()["user"]["login"] == "reader"
    assert response.json()["csrf_token"] != csrf
    assert auth_client.cookies["__Host-rag_session"] != preauth
    assert "__Host-rag_preauth" not in auth_client.cookies
    assert auth_client.get("/api/auth/me").status_code == 200


def test_logout_accepts_bodyless_protocol(auth_client: TestClient) -> None:
    csrf = CsrfResponse.model_validate_json(auth_client.get("/api/auth/csrf").content).csrf_token
    login = auth_client.post(
        "/api/auth/login",
        json={"login": "reader", "password": "Synthetic-Pass-123"},
        headers={"Origin": "https://rag.test", "X-CSRF-Token": csrf},
    )
    token = LoginResponse.model_validate_json(login.content).csrf_token
    response = auth_client.post(
        "/api/auth/logout", headers={"Origin": "https://rag.test", "X-CSRF-Token": token}
    )
    assert response.status_code == 204
    assert auth_client.get("/api/auth/me").status_code == 401


def test_logout_rejects_unexpected_body(auth_client: TestClient) -> None:
    csrf = CsrfResponse.model_validate_json(auth_client.get("/api/auth/csrf").content).csrf_token
    login = auth_client.post(
        "/api/auth/login",
        json={"login": "reader", "password": "Synthetic-Pass-123"},
        headers={"Origin": "https://rag.test", "X-CSRF-Token": csrf},
    )
    token = LoginResponse.model_validate_json(login.content).csrf_token
    response = auth_client.post(
        "/api/auth/logout",
        json={"is_admin": True},
        headers={"Origin": "https://rag.test", "X-CSRF-Token": token},
    )
    assert response.status_code == 422
    assert auth_client.get("/api/auth/me").status_code == 200
