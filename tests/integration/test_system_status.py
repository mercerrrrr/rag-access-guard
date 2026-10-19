import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from starlette.types import Message, Scope


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


@pytest.mark.parametrize(
    "path",
    [
        "/api/system/status",
        "/api/system/evaluation-identity",
        "/api/demo/profile",
        "/api/chat/suggestions",
    ],
)
def test_authenticated_metadata_disconnect_is_safe_http_error(
    authenticated_client: TestClient, path: str
) -> None:
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"rag.test"),
            (
                b"cookie",
                "; ".join(
                    f"{key}={value}" for key, value in authenticated_client.cookies.items()
                ).encode(),
            ),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("rag.test", 443),
    }
    statuses: list[int] = []
    headers: list[tuple[bytes, bytes]] = []

    async def disconnected() -> Message:
        return {"type": "http.disconnect"}

    async def record(message: Message) -> None:
        if message["type"] == "http.response.start":
            statuses.append(TypeAdapter(int).validate_python(message["status"]))
            headers.extend(
                TypeAdapter(list[tuple[bytes, bytes]]).validate_python(message["headers"])
            )

    portal = authenticated_client.portal
    assert portal is not None
    portal.call(authenticated_client.app, scope, disconnected, record)
    assert statuses == [400]
    assert (b"cache-control", b"private, no-store") in headers
    assert (b"vary", b"Cookie") in headers
