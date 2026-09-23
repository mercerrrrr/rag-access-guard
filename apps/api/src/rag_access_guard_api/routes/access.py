"""Version-scoped document reading."""

from typing import assert_never
from uuid import UUID

from fastapi import APIRouter, Request, Response

from rag_access_guard_api.config import Settings
from rag_access_guard_api.routes.auth import AuthCookies, check_admin_mutation, check_origin
from rag_access_guard_api.schemas.access import (
    AccessibleDocuments,
    DirectGrantRequest,
    DocumentText,
    GrantList,
    GrantView,
    RoleCreate,
    RoleGrantRequest,
    RoleList,
    RolePatch,
    RoleView,
    UserList,
)
from rag_access_guard_api.services import roles
from rag_access_guard_api.services.access import (
    DocumentVersionRef,
    list_accessible_documents,
    read_document_text,
)
from rag_access_guard_api.services.grants import (
    grant_role,
    grant_user,
    list_grants,
    list_users,
    revoke_grant,
)
from rag_access_guard_api.services.security import PolicyUnitOfWork


def build_access_router(policy: PolicyUnitOfWork, settings: Settings) -> APIRouter:
    """Bind document reads to the session transaction."""
    router = APIRouter()
    cookies = AuthCookies(settings.loopback_development)

    @router.get("/api/documents/{document_id}/versions/{version_id}/text")
    async def read_text(document_id: UUID, version_id: UUID, request: Request) -> DocumentText:
        """Read the stored canonical version text."""
        check_origin(request, settings)
        async with policy.protected_read(cookies.credentials(request).session) as uow:
            return await read_document_text(uow, DocumentVersionRef(document_id, version_id))

    @router.get("/api/documents")
    async def documents(request: Request) -> AccessibleDocuments:
        """List only documents visible through the current session."""
        check_origin(request, settings)
        async with policy.protected_read(cookies.credentials(request).session) as uow:
            return AccessibleDocuments(items=await list_accessible_documents(uow))

    @router.get("/api/admin/users")
    async def users(request: Request) -> UserList:
        """Select grant recipients without exposing authentication data."""
        check_origin(request, settings)
        async with policy.protected_read(cookies.credentials(request).session) as uow:
            return UserList(items=await list_users(uow))

    @router.get("/api/admin/documents/{document_id}/grants")
    async def grants(document_id: UUID, request: Request) -> GrantList:
        """List grants after a fresh administrator gate."""
        check_origin(request, settings)
        async with policy.protected_read(cookies.credentials(request).session) as uow:
            return GrantList(items=await list_grants(uow, document_id))

    @router.post("/api/admin/documents/{document_id}/grants", status_code=201)
    async def grant(
        document_id: UUID, payload: DirectGrantRequest | RoleGrantRequest, request: Request
    ) -> GrantView:
        """Commit the new grant, revision and audit before returning it."""
        check_origin(request, settings, unsafe=True)
        credentials = cookies.credentials(request)
        async with policy.mutation(credentials.session) as uow:
            check_admin_mutation(uow, credentials.csrf)
            match payload:
                case DirectGrantRequest(user_id=user_id):
                    return await grant_user(uow, document_id, user_id)
                case RoleGrantRequest(role_id=role_id):
                    return await grant_role(uow, document_id, role_id)
                case _:
                    assert_never(payload)

    @router.delete("/api/admin/documents/{document_id}/grants/{grant_id}", status_code=204)
    async def revoke(document_id: UUID, grant_id: UUID, request: Request) -> Response:
        """Revoke only the path named by both document and grant identifiers."""
        check_origin(request, settings, unsafe=True)
        credentials = cookies.credentials(request)
        async with policy.mutation(credentials.session) as uow:
            check_admin_mutation(uow, credentials.csrf)
            await revoke_grant(uow, document_id, grant_id)
        return Response(status_code=204)

    return router


def build_role_router(policy: PolicyUnitOfWork, settings: Settings) -> APIRouter:
    """Bind administrative role management to the existing session gates."""
    router = APIRouter()
    cookies = AuthCookies(settings.loopback_development)

    @router.get("/api/admin/roles")
    async def role_list(request: Request) -> RoleList:
        """List roles after a fresh administrative gate."""
        check_origin(request, settings)
        async with policy.protected_read(cookies.credentials(request).session) as uow:
            return RoleList(items=await roles.list_roles(uow))

    @router.post("/api/admin/roles", status_code=201)
    async def create_role(payload: RoleCreate, request: Request) -> RoleView:
        """Commit a new role with its audit event."""
        check_origin(request, settings, unsafe=True)
        credentials = cookies.credentials(request)
        async with policy.mutation(credentials.session) as uow:
            check_admin_mutation(uow, credentials.csrf)
            return await roles.create_role(
                uow, code=payload.code, display_name=payload.display_name
            )

    @router.patch("/api/admin/roles/{role_id}")
    async def update_role(role_id: UUID, payload: RolePatch, request: Request) -> RoleView:
        """Update the display label without changing the role identity."""
        check_origin(request, settings, unsafe=True)
        credentials = cookies.credentials(request)
        async with policy.mutation(credentials.session) as uow:
            check_admin_mutation(uow, credentials.csrf)
            return await roles.update_role(uow, role_id, payload.display_name)

    @router.get("/api/admin/roles/{role_id}/members")
    async def members(role_id: UUID, request: Request) -> UserList:
        """List members without exposing their credentials."""
        check_origin(request, settings)
        async with policy.protected_read(cookies.credentials(request).session) as uow:
            return UserList(items=await roles.list_members(uow, role_id))

    @router.put("/api/admin/roles/{role_id}/members/{user_id}", status_code=204)
    async def add_member(role_id: UUID, user_id: UUID, request: Request) -> Response:
        """Ensure membership exists, recording only an effective change."""
        check_origin(request, settings, unsafe=True)
        credentials = cookies.credentials(request)
        async with policy.mutation(credentials.session) as uow:
            check_admin_mutation(uow, credentials.csrf)
            await roles.set_membership(uow, role_id, user_id, present=True)
        return Response(status_code=204)

    @router.delete("/api/admin/roles/{role_id}/members/{user_id}", status_code=204)
    async def remove_member(role_id: UUID, user_id: UUID, request: Request) -> Response:
        """Remove membership without revoking independent document grants."""
        check_origin(request, settings, unsafe=True)
        credentials = cookies.credentials(request)
        async with policy.mutation(credentials.session) as uow:
            check_admin_mutation(uow, credentials.csrf)
            await roles.set_membership(uow, role_id, user_id, present=False)
        return Response(status_code=204)

    return router
