"""Session-bound conversation metadata; generation is not exposed here."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from rag_access_guard_api.config import Settings
from rag_access_guard_api.routes.auth import AuthCookies, check_origin
from rag_access_guard_api.schemas.chat import CreateThread, ThreadDetail, ThreadView
from rag_access_guard_api.services.chat_repository import (
    create_thread,
    get_owned_thread,
    list_threads,
)
from rag_access_guard_api.services.errors import ForbiddenError
from rag_access_guard_api.services.security import PolicyUnitOfWork
from rag_access_guard_api.services.tokens import matches_token


def build_chat_router(policy: PolicyUnitOfWork, settings: Settings) -> APIRouter:
    """Bind every operation to the same policy-first session gate."""
    router = APIRouter(prefix="/api/chat/threads", tags=["chat"])
    cookies = AuthCookies(settings.loopback_development)

    @router.post("", status_code=201)
    async def create(payload: CreateThread, request: Request) -> ThreadView:
        """Accept an empty payload; owner and initial title are server assigned."""
        del payload
        check_origin(request, settings, unsafe=True)
        if request.query_params:
            raise HTTPException(422, "Invalid request")
        credentials = cookies.credentials(request)
        async with policy.protected_read(credentials.session) as uow:
            if not matches_token(credentials.csrf, uow.csrf_digest):
                raise ForbiddenError
            return await create_thread(uow)

    @router.get("")
    async def listing(request: Request) -> tuple[ThreadView, ...]:
        """Return no foreign metadata, including for administrative sessions."""
        check_origin(request, settings)
        if request.query_params:
            raise HTTPException(422, "Invalid request")
        async with policy.protected_read(cookies.credentials(request).session) as uow:
            return await list_threads(uow)

    @router.get("/{thread_id}")
    async def detail(thread_id: UUID, request: Request) -> ThreadDetail:
        """Expose owned thread metadata without reading stored model output."""
        check_origin(request, settings)
        if request.query_params:
            raise HTTPException(422, "Invalid request")
        async with policy.protected_read(cookies.credentials(request).session) as uow:
            thread = await get_owned_thread(uow, thread_id)
            return ThreadDetail(
                id=thread.id,
                title=thread.title,
                revision=thread.revision,
                created_at=thread.created_at,
                turns=(),
            )

    return router
