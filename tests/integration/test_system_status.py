import pytest
from fastapi.testclient import TestClient


def test_system_status_requires_current_session(auth_client: TestClient) -> None:
    response = auth_client.get("/api/system/status")
    assert response.status_code == 401
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["vary"] == "Cookie"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_disabled_system_status_exposes_only_safe_readiness(
    authenticated_client: TestClient,
) -> None:
    response = authenticated_client.get("/api/system/status")
    assert response.status_code == 200
    assert response.json() == {
        "mode": "disabled",
        "model": {"name": None, "state": "disabled"},
        "search": {"state": "ready"},
    }
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize("suffix", ["?model=qwen3:4b", "?profile=anything", "?url=http://remote"])
def test_system_status_rejects_client_selection(
    authenticated_client: TestClient,
    suffix: str,
) -> None:
    response = authenticated_client.get(f"/api/system/status{suffix}")
    assert response.status_code == 422
    assert response.headers["cache-control"] == "private, no-store"


def test_system_status_rejects_body(authenticated_client: TestClient) -> None:
    response = authenticated_client.request("GET", "/api/system/status", json={"model": "other"})
    assert response.status_code == 422


def test_cached_status_still_authenticates_after_logout(authenticated_client: TestClient) -> None:
    assert authenticated_client.get("/api/system/status").status_code == 200
    response = authenticated_client.post(
        "/api/auth/logout",
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": authenticated_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 204
    response = authenticated_client.get("/api/system/status")
    assert response.status_code == 401
    assert response.headers["cache-control"] == "private, no-store"
