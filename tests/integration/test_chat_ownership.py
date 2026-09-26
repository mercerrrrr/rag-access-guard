import pytest
from pydantic import TypeAdapter
from sqlalchemy import text

from rag_access_guard_api.schemas.chat import ThreadDetail, ThreadView
from tests.support.chat import ChatHttp


def test_foreign_thread_is_indistinguishable_from_missing(chat_http: ChatHttp) -> None:
    created = chat_http.owner.post(
        "/api/chat/threads", json={}, headers=chat_http.csrf(chat_http.owner)
    )
    assert created.status_code == 201
    foreign = chat_http.other.get(f"/api/chat/threads/{created.json()['id']}")
    missing = chat_http.other.get("/api/chat/threads/00000000-0000-0000-0000-000000000099")
    assert (foreign.status_code, foreign.json()) == (missing.status_code, missing.json())
    assert foreign.status_code == 404
    for response in (created, foreign, missing):
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["vary"] == "Cookie"


def test_each_user_lists_only_owned_threads(chat_http: ChatHttp) -> None:
    owner_ids = [
        chat_http.owner.post(
            "/api/chat/threads", json={}, headers=chat_http.csrf(chat_http.owner)
        ).json()["id"]
        for _ in range(2)
    ]
    other_id = str(
        ThreadView.model_validate_json(
            chat_http.other.post(
                "/api/chat/threads", json={}, headers=chat_http.csrf(chat_http.other)
            ).content
        ).id
    )
    owner_list = chat_http.owner.get("/api/chat/threads")
    other_list = chat_http.other.get("/api/chat/threads")
    assert owner_list.status_code == other_list.status_code == 200
    adapter = TypeAdapter(tuple[ThreadView, ...])
    assert [str(item.id) for item in adapter.validate_json(owner_list.content)] == owner_ids
    assert [str(item.id) for item in adapter.validate_json(other_list.content)] == [other_id]


def test_create_returns_server_metadata_and_empty_owned_detail(chat_http: ChatHttp) -> None:
    with chat_http.database.connect() as connection:
        revision = TypeAdapter(int).validate_python(
            connection.execute(text("SELECT revision FROM policy_state")).scalar_one()
        )
    response = chat_http.owner.post(
        "/api/chat/threads", json={}, headers=chat_http.csrf(chat_http.owner)
    )
    assert response.status_code == 201
    created = ThreadView.model_validate_json(response.content)
    detail = chat_http.owner.get(f"/api/chat/threads/{created.id}")
    assert detail.status_code == 200
    assert ThreadDetail.model_validate_json(detail.content).turns == ()
    assert created.title == "Новый диалог"
    assert created.revision == 0
    assert created.created_at.utcoffset() is not None
    assert set(TypeAdapter(dict[str, object]).validate_json(response.content)) == {
        "id",
        "title",
        "revision",
        "created_at",
    }
    with chat_http.database.connect() as connection:
        assert (
            connection.execute(text("SELECT revision FROM policy_state")).scalar_one() == revision
        )
        assert (
            connection.execute(
                text("""SELECT u.login FROM chat_threads t
            JOIN users u ON u.id=t.owner_user_id""")
            ).scalar_one()
            == "reader"
        )
        assert connection.execute(text("SELECT count(*) FROM chat_turns")).scalar_one() == 0


def test_admin_cannot_read_foreign_chat(chat_http: ChatHttp) -> None:
    created = ThreadView.model_validate_json(
        chat_http.owner.post(
            "/api/chat/threads", json={}, headers=chat_http.csrf(chat_http.owner)
        ).content
    ).id
    with chat_http.database.begin() as connection:
        _ = connection.execute(text("UPDATE users SET is_admin=true WHERE login='other'"))
    assert chat_http.other.get(f"/api/chat/threads/{created}").status_code == 404
    assert chat_http.other.get("/api/chat/threads").json() == []


@pytest.mark.parametrize(
    "field",
    ["owner_id", "owner_user_id", "principal_id", "user_id", "roles", "title", "revision", "turns"],
)
def test_client_cannot_set_thread_identity_or_content(field: str, chat_http: ChatHttp) -> None:
    response = chat_http.owner.post(
        "/api/chat/threads", json={field: "private-value"}, headers=chat_http.csrf(chat_http.owner)
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid request"}
    assert "private-value" not in response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert chat_http.owner.get("/api/chat/threads").json() == []


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "https://rag.test"},
        {"Origin": "https://foreign.test", "X-CSRF-Token": "invalid"},
    ],
)
def test_create_requires_exact_origin_and_session_csrf(
    headers: dict[str, str], chat_http: ChatHttp
) -> None:
    response = chat_http.owner.post("/api/chat/threads", json={}, headers=headers)
    assert response.status_code == 403
    assert response.headers["cache-control"] == "private, no-store"
    assert chat_http.owner.get("/api/chat/threads").json() == []


def test_anonymous_chat_operations_are_denied(chat_http: ChatHttp) -> None:
    chat_http.other.cookies.clear()
    for method, path in (
        ("GET", "/api/chat/threads"),
        ("GET", "/api/chat/threads/00000000-0000-0000-0000-000000000099"),
        ("POST", "/api/chat/threads"),
    ):
        response = chat_http.other.request(
            method, path, json={}, headers={"Origin": "https://rag.test"}
        )
        assert response.status_code == 401
        assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize("expiry", ["idle", "absolute", "inactive", "logout"])
def test_chat_access_rechecks_session(expiry: str, chat_http: ChatHttp) -> None:
    created = ThreadView.model_validate_json(
        chat_http.owner.post(
            "/api/chat/threads", json={}, headers=chat_http.csrf(chat_http.owner)
        ).content
    ).id
    statements = {
        "idle": """UPDATE sessions SET created_at=clock_timestamp()-interval '1 hour',
        last_seen_at=clock_timestamp()-interval '31 minutes'""",
        "absolute": "UPDATE sessions SET absolute_expires_at=clock_timestamp()",
        "inactive": "UPDATE users SET is_active=false WHERE login='reader'",
    }
    csrf = chat_http.csrf(chat_http.owner)
    if expiry == "logout":
        token = chat_http.owner.cookies["__Host-rag_session"]
        assert chat_http.owner.post("/api/auth/logout", headers=csrf).status_code == 204
        chat_http.owner.cookies.set("__Host-rag_session", token, domain="rag.test", path="/")
    else:
        with chat_http.database.begin() as connection:
            _ = connection.execute(text(statements[expiry]))
    for path in ("/api/chat/threads", f"/api/chat/threads/{created}"):
        response = chat_http.owner.get(path)
        assert response.status_code == 401
        assert response.headers["cache-control"] == "private, no-store"
    assert chat_http.owner.post("/api/chat/threads", json={}, headers=csrf).status_code == 401


def test_query_identity_cannot_override_session(chat_http: ChatHttp) -> None:
    response = chat_http.other.get("/api/chat/threads?user_id=reader")
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid request"}


def test_generation_route_rejects_client_supplied_answer(chat_http: ChatHttp) -> None:
    created = ThreadView.model_validate_json(
        chat_http.owner.post(
            "/api/chat/threads", json={}, headers=chat_http.csrf(chat_http.owner)
        ).content
    ).id
    response = chat_http.owner.post(
        f"/api/chat/threads/{created}/messages",
        json={"answer": "injected"},
        headers=chat_http.csrf(chat_http.owner),
    )
    assert response.status_code == 422
    assert response.headers["cache-control"] == "private, no-store"
