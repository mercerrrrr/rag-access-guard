"""Exact-origin authentication protocol and host-only cookie handling."""

from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, Request, Response

from rag_access_guard_api.config import Settings, is_loopback
from rag_access_guard_api.schemas.auth import (
    CsrfResponse,
    LoginRequest,
    LoginResponse,
    UserResponse,
)
from rag_access_guard_api.services.auth import AuthCredentials, AuthService


@dataclass(frozen=True, slots=True)
class AuthCookies:
    """Cookie names and flags are selected only by explicit deployment mode."""

    local: bool

    def name(self, kind: str) -> str:
        """Keep development cookies separate from HTTPS host cookies."""
        return f"rag_{kind}_local" if self.local else f"__Host-rag_{kind}"

    def set(self, response: Response, kind: str, value: str) -> None:
        """Emit host-only cookies with consistent authentication flags."""
        response.set_cookie(
            self.name(kind),
            value,
            secure=not self.local,
            httponly=kind != "csrf",
            samesite="lax",
            path="/",
        )

    def delete(self, response: Response, kind: str) -> None:
        """Expire credentials using the same flags as creation."""
        response.delete_cookie(
            self.name(kind),
            secure=not self.local,
            httponly=kind != "csrf",
            samesite="lax",
            path="/",
        )

    def credentials(self, request: Request, *, bootstrap: bool = False) -> AuthCredentials:
        """Read raw tokens solely from protocol cookie/header channels."""
        return AuthCredentials(
            session=request.cookies.get(self.name("session"), ""),
            preauth=request.cookies.get(self.name("preauth"), ""),
            csrf=(
                request.cookies.get(self.name("csrf"), "")
                if bootstrap
                else request.headers.get("x-csrf-token", "")
            ),
        )


def check_origin(request: Request, settings: Settings, *, unsafe: bool = False) -> None:
    """Deny missing unsafe origins and every explicit foreign origin."""
    origin = request.headers.get("origin")
    if ((unsafe or origin is not None) and origin != settings.auth_origin) or (
        settings.loopback_development and not is_loopback(request.url.hostname or "")
    ):
        raise HTTPException(status_code=403, detail="Forbidden")


def peer(request: Request) -> str:
    """Trust the transport peer, never forwarded headers."""
    if request.client is None:
        raise HTTPException(status_code=403, detail="Forbidden")
    return request.client.host


def build_auth_router(service: AuthService, settings: Settings) -> APIRouter:
    """Bind HTTP routes to one application-local authentication service."""
    router = APIRouter(prefix="/api/auth", tags=["auth"])
    cookies = AuthCookies(settings.loopback_development)

    @router.get("/csrf")
    async def csrf(request: Request, response: Response) -> CsrfResponse:
        """Restore session CSRF or issue an unprivileged login challenge."""
        check_origin(request, settings)
        if request.query_params:
            raise HTTPException(status_code=403, detail="Forbidden")
        preauth, token = await service.bootstrap(
            cookies.credentials(request, bootstrap=True), peer(request)
        )
        if preauth is not None:
            cookies.set(response, "preauth", preauth)
        cookies.set(response, "csrf", token)
        return CsrfResponse(csrf_token=token)

    @router.post("/login")
    async def login(payload: LoginRequest, request: Request, response: Response) -> LoginResponse:
        """Create a fresh session only after challenge and credential gates."""
        check_origin(request, settings, unsafe=True)
        if (
            request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            != "application/json"
        ):
            raise HTTPException(status_code=415, detail="Expected application/json")
        token, result = await service.login(payload, cookies.credentials(request), peer(request))
        cookies.set(response, "session", token)
        cookies.set(response, "csrf", result.csrf_token)
        cookies.delete(response, "preauth")
        return result

    @router.get("/me")
    async def me(request: Request) -> UserResponse:
        """Expose fresh account metadata, never the bearer token."""
        check_origin(request, settings)
        return UserResponse(user=await service.me(cookies.credentials(request).session))

    @router.post("/logout", status_code=204)
    async def logout(request: Request) -> Response:
        """Revoke atomically before removing browser credentials."""
        check_origin(request, settings, unsafe=True)
        if await request.body():
            raise HTTPException(status_code=422, detail="Invalid request")
        await service.logout(cookies.credentials(request))
        response = Response(status_code=204)
        for kind in ("session", "preauth", "csrf"):
            cookies.delete(response, kind)
        return response

    return router
