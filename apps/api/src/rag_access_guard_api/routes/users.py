"""Session-bound administrative user security operations."""

from uuid import UUID

from fastapi import APIRouter, Request, Response

from rag_access_guard_api.config import Settings
from rag_access_guard_api.routes.auth import AuthCookies, check_admin_mutation, check_origin
from rag_access_guard_api.schemas.access import UserSecurityPatch, UserSummary
from rag_access_guard_api.services.security import PolicyUnitOfWork
from rag_access_guard_api.services.users import revoke_user_session, update_user_security


def build_users_router(policy: PolicyUnitOfWork, settings: Settings) -> APIRouter:
    """Require fresh administrative authority for every identity mutation."""
    router = APIRouter(prefix="/api/admin/users", tags=["users"])
    cookies = AuthCookies(settings.loopback_development)

    @router.patch("/{user_id}")
    async def patch(user_id: UUID, payload: UserSecurityPatch, request: Request) -> UserSummary:
        """Commit security flags and audit before returning metadata."""
        check_origin(request, settings, unsafe=True)
        credentials = cookies.credentials(request)
        async with policy.mutation(credentials.session) as uow:
            check_admin_mutation(uow, credentials.csrf)
            return await update_user_security(
                uow, user_id, is_active=payload.is_active, is_admin=payload.is_admin
            )

    @router.delete("/{user_id}/sessions/{session_id}", status_code=204)
    async def revoke(user_id: UUID, session_id: UUID, request: Request) -> Response:
        """Bind revocation to both path identifiers and current administrator."""
        check_origin(request, settings, unsafe=True)
        credentials = cookies.credentials(request)
        async with policy.mutation(credentials.session) as uow:
            check_admin_mutation(uow, credentials.csrf)
            await revoke_user_session(uow, user_id, session_id)
        return Response(status_code=204)

    return router
