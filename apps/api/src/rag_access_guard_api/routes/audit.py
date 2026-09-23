"""Read-only, session-protected audit endpoint."""

from binascii import Error as Base64Error
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request

from rag_access_guard_api.config import Settings
from rag_access_guard_api.routes.auth import AuthCookies, check_origin
from rag_access_guard_api.schemas.audit import AuditPage, AuditQuery, decode_cursor
from rag_access_guard_api.services.audit_read import read_audit
from rag_access_guard_api.services.security import PolicyUnitOfWork


def build_audit_router(policy: PolicyUnitOfWork, settings: Settings) -> APIRouter:
    """Bind audit pagination to the same fresh session and administrator gate."""
    router = APIRouter(prefix="/api/admin/audit", tags=["audit"])
    cookies = AuthCookies(settings.loopback_development)

    @router.get("")
    async def listing(query: Annotated[AuditQuery, Query()], request: Request) -> AuditPage:
        """Validate cursors without exposing their rejected payload."""
        check_origin(request, settings)
        try:
            cursor = decode_cursor(query.cursor)
        except (ValueError, Base64Error):
            raise HTTPException(status_code=422, detail="Invalid request") from None
        async with policy.protected_read(cookies.credentials(request).session) as uow:
            return await read_audit(uow, query, cursor)

    return router
