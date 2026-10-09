"""Authenticate each readiness read, with probes outside the policy transaction."""

from fastapi import APIRouter, HTTPException, Request

from rag_access_guard_api.config import Settings
from rag_access_guard_api.routes.auth import AuthCookies, check_origin
from rag_access_guard_api.schemas.system import SystemStatus
from rag_access_guard_api.services.security import PolicyUnitOfWork
from rag_access_guard_api.services.system_status import SystemReadiness


def build_system_router(policy: PolicyUnitOfWork, settings: Settings) -> APIRouter:
    """Retain a readiness cache, never a grant or session cache."""
    router = APIRouter(prefix="/api/system", tags=["system"])
    cookies = AuthCookies(settings.loopback_development)
    readiness = SystemReadiness(settings)

    @router.get("/status")
    async def status(request: Request) -> SystemStatus:
        check_origin(request, settings)
        credentials = cookies.credentials(request)
        async with policy.protected_read(credentials.session):
            pass
        if request.query_params or await request.body():
            raise HTTPException(422, "Invalid request")
        result = await readiness.snapshot()
        async with policy.protected_read(credentials.session):
            return result

    return router
