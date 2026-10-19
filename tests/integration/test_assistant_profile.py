import pytest
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect, Request


@pytest.mark.parametrize("path", ["/api/demo/profile", "/api/chat/suggestions"])
def test_guidance_routes_require_active_session_and_reject_foreign_origin(
    auth_client: TestClient, authenticated_client: TestClient, path: str
) -> None:
    response = authenticated_client.get(path, headers={"Origin": "https://foreign.test"})
    assert response.status_code == 403
    auth_client.cookies.clear()
    response = auth_client.get(path)
    assert response.status_code == 401
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["vary"] == "Cookie"


def test_profile_is_generic_and_has_no_asset_endpoint(authenticated_client: TestClient) -> None:
    response = authenticated_client.get("/api/demo/profile")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json() == {
        "enabled": False,
        "title": "Помощник по базе знаний",
        "subtitle": None,
        "notice": "Исследовательский прототип",
        "logo_url": None,
    }
    assert authenticated_client.get("/api/demo/brand/logo").status_code == 404


@pytest.mark.parametrize("path", ["/api/demo/profile", "/api/chat/suggestions"])
def test_guidance_rejects_unexpected_inputs_with_no_store(
    authenticated_client: TestClient, path: str
) -> None:
    for response in (
        authenticated_client.get(path + "?filename=private"),
        authenticated_client.request("GET", path, content=b"private"),
    ):
        assert response.status_code == 422
        assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize("path", ["/api/demo/profile", "/api/chat/suggestions"])
def test_guidance_body_disconnect_returns_safe_error_instead_of_asgi_exception(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    assert authenticated_client.get(path).status_code == 200

    async def disconnected_body(_request: Request) -> bytes:
        raise ClientDisconnect

    monkeypatch.setattr(Request, "body", disconnected_body)
    response = authenticated_client.get(path)
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid request"}
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["vary"] == "Cookie"
