from uuid import UUID

import anyio
import httpx2
import pytest
from experiments.demo_sessions import DemoActorSessions
from pydantic import SecretStr

from rag_access_guard_api.demo.contracts import DemoMarker
from rag_access_guard_api.demo.credentials import CredentialEntry, CredentialJournal
from rag_access_guard_api.demo.evaluation import DemoDatasetError
from rag_access_guard_api.schemas.auth import LoginRequest


def actor_journal() -> CredentialJournal:
    return CredentialJournal(
        marker=DemoMarker(
            instance_id=UUID(int=1),
            dataset_id="synthetic",
            database_name="synthetic",
            database_role="synthetic",
        ),
        entries=tuple(
            CredentialEntry(login=login, password=SecretStr("synthetic"))
            for login in ("alpha", "beta")
        ),
    )


def wire_client(paths: list[str], origin: str = "http://127.0.0.1:8019") -> httpx2.AsyncClient:
    def response(request: httpx2.Request) -> httpx2.Response:
        paths.append(request.url.path)
        if request.url.path == "/api/auth/csrf":
            return httpx2.Response(200, json={"csrf_token": "challenge"})
        if request.url.path == "/api/auth/login":
            login = LoginRequest.model_validate_json(request.content).login
            return httpx2.Response(
                200,
                json={
                    "user": {
                        "id": str(UUID(int=1)),
                        "login": login,
                        "display_name": "Synthetic",
                        "is_admin": False,
                    },
                    "csrf_token": f"csrf-{login}",
                },
                headers={"set-cookie": f"rag_session_local=session-{login}; Path=/"},
            )
        return httpx2.Response(200)

    return httpx2.AsyncClient(base_url=origin, transport=httpx2.MockTransport(response))


def test_actor_tokens_and_csrf_are_separate_and_never_reauthenticated_by_bind() -> None:
    # Given two ordinary preflight logins on separate clients in one run.
    paths: list[str] = []
    sessions = DemoActorSessions(actor_journal())

    async def run() -> None:
        for login in ("alpha", "beta"):
            async with wire_client(paths) as client:
                _ = await sessions.authenticate(client, login)
        # When each unit binds only its own cached authentication material.
        for login in ("alpha", "beta"):
            async with wire_client(paths) as client:
                token = sessions.bind(client, login)
                assert token == client.cookies.get("rag_session_local") == f"session-{login}"
                assert client.headers["X-CSRF-Token"] == f"csrf-{login}"
        # Then no per-unit bootstrap/login occurs and repr reveals neither secret.
        assert paths == ["/api/auth/csrf", "/api/auth/login"] * 2
        assert "session-alpha" not in repr(sessions)
        assert "csrf-beta" not in repr(sessions)

    anyio.run(run)


@pytest.mark.parametrize(
    "origin",
    [
        "http://127.0.0.1:8020",
        "http://localhost:8019",
        "https://127.0.0.1:8019",
        "http://example.com:8019",
    ],
)
def test_cached_authentication_rejects_different_origin_before_binding(origin: str) -> None:
    # Given authentication bound to one exact loopback scheme/host/port.
    paths: list[str] = []
    sessions = DemoActorSessions(actor_journal())

    async def run() -> None:
        async with wire_client(paths) as client:
            _ = await sessions.authenticate(client, "alpha")
        # When a different origin attempts to acquire that actor's secrets.
        async with wire_client(paths, origin) as other:
            with pytest.raises(DemoDatasetError, match="actor_session_origin"):
                _ = sessions.bind(other, "alpha")
            # Then no credentials are installed or sent to that origin.
            assert other.cookies.get("rag_session_local") is None
            assert "X-CSRF-Token" not in other.headers
        assert paths == ["/api/auth/csrf", "/api/auth/login"]

    anyio.run(run)


def test_bound_client_cannot_forward_credentials_to_an_absolute_other_port() -> None:
    # Given a correctly bound client whose callers normally use relative API paths.
    paths: list[str] = []
    sessions = DemoActorSessions(actor_journal())

    async def run() -> None:
        async with wire_client(paths) as client:
            _ = await sessions.authenticate(client, "alpha")
        async with wire_client(paths) as client:
            _ = sessions.bind(client, "alpha")
            # When an absolute URL tries to leave the authenticated origin.
            with pytest.raises(DemoDatasetError, match="actor_session_origin"):
                _ = await client.get("http://127.0.0.1:8020/api/auth/me")
        # Then the request hook denies before transport receives the credentialed request.
        assert paths == ["/api/auth/csrf", "/api/auth/login"]

    anyio.run(run)


def test_unknown_or_another_run_actor_cannot_trigger_login() -> None:
    # Given one authenticated actor and a new run with no authenticated actors.
    paths: list[str] = []
    sessions = DemoActorSessions(actor_journal())

    async def run() -> None:
        async with wire_client(paths) as client:
            _ = await sessions.authenticate(client, "alpha")
        # When missing, unknown or previous-run sessions are requested.
        for owner, login in (
            (sessions, "beta"),
            (sessions, "missing"),
            (DemoActorSessions(actor_journal()), "alpha"),
        ):
            async with wire_client(paths) as client:
                with pytest.raises(DemoDatasetError, match="actor_session_absent"):
                    _ = owner.bind(client, login)
        # Then none silently reacquires credentials or refreshes a session.
        assert paths == ["/api/auth/csrf", "/api/auth/login"]

    anyio.run(run)


def test_standard_http_port_preserves_the_same_origin_after_url_normalization() -> None:
    # Given an explicit configured port80 normalized by the HTTP client's URL parser.
    paths: list[str] = []
    sessions = DemoActorSessions(actor_journal())

    async def run() -> None:
        async with wire_client(paths, "http://127.0.0.1:80") as client:
            _ = await sessions.authenticate(client, "alpha")
        # When the equivalent canonical default-port origin binds the authenticated actor.
        async with wire_client(paths, "http://127.0.0.1") as client:
            session = sessions.bind(client, "alpha")
            assert session == client.cookies.get("rag_session_local") == "session-alpha"
        # Then URL normalization does not reject a valid DemoRuntime port or reacquire login.
        assert paths == ["/api/auth/csrf", "/api/auth/login"]

    anyio.run(run)
