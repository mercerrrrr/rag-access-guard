"""Per-run authentication material, never authorization snapshots or session renewal."""

from dataclasses import dataclass, field
from typing import Final

import httpx2
from pydantic import SecretStr

from experiments.demo_http import credential_for, login_actor
from rag_access_guard_api.config import is_loopback
from rag_access_guard_api.demo.credentials import CredentialJournal
from rag_access_guard_api.demo.evaluation import DemoDatasetError

HTTP_DEFAULT_PORT: Final = 80


def _origin(url: httpx2.URL) -> tuple[str, str, int]:
    if url.scheme != "http" or not is_loopback(url.host) or url.username or url.password:
        raise DemoDatasetError(code="actor_session_origin")
    return url.scheme, url.host, url.port if url.port is not None else HTTP_DEFAULT_PORT


def _restrict_origin(client: httpx2.AsyncClient, origin: tuple[str, str, int]) -> None:
    async def guard(request: httpx2.Request) -> None:
        if _origin(request.url) != origin:
            raise DemoDatasetError(code="actor_session_origin")

    client.event_hooks["request"].append(guard)


@dataclass(frozen=True, slots=True)
class ActorAuthentication:
    """The exact preflight origin and repr-safe bearer/CSRF secrets; nothing else is cached."""

    origin: tuple[str, str, int]
    session: SecretStr = field(repr=False)
    csrf: SecretStr = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class DemoActorSessions:
    """Enroll actors once during preflight; unit binding cannot log in, refresh or authorize."""

    journal: CredentialJournal = field(repr=False)
    _actors: dict[str, ActorAuthentication] = field(default_factory=dict, init=False, repr=False)

    async def authenticate(self, client: httpx2.AsyncClient, login: str) -> str:
        """Perform the ordinary preflight login once, with exact-origin request confinement."""
        if login in self._actors:
            raise DemoDatasetError(code="actor_already_authenticated")
        origin = _origin(client.base_url)
        _restrict_origin(client, origin)
        session = await login_actor(client, credential_for(self.journal, login))
        csrf = client.headers.get("X-CSRF-Token")
        if not session or not csrf:
            raise DemoDatasetError(code="actor_session_material_absent")
        self._actors[login] = ActorAuthentication(origin, SecretStr(session), SecretStr(csrf))
        return session

    def bind(self, client: httpx2.AsyncClient, login: str) -> str:
        """Install only an already authenticated actor on a fresh same-origin client."""
        authentication = self._actors.get(login)
        if authentication is None:
            raise DemoDatasetError(code="actor_session_absent")
        if _origin(client.base_url) != authentication.origin:
            raise DemoDatasetError(code="actor_session_origin")
        _restrict_origin(client, authentication.origin)
        session = authentication.session.get_secret_value()
        client.cookies.set("rag_session_local", session, domain=client.base_url.host, path="/")
        client.headers["X-CSRF-Token"] = authentication.csrf.get_secret_value()
        return session
