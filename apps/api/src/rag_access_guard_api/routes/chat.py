"""Session-owned chat with committed generation and reauthorized stored answers."""

from typing import assert_never
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from rag_access_guard_api.config import Settings
from rag_access_guard_api.routes.auth import AuthCookies, check_origin
from rag_access_guard_api.schemas.chat import (
    CreateThread,
    MessageRequest,
    MessageResponse,
    ThreadDetail,
    ThreadView,
)
from rag_access_guard_api.services.chat import ChatService
from rag_access_guard_api.services.chat_read import read_turn
from rag_access_guard_api.services.chat_repository import (
    create_thread,
    get_owned_thread,
    list_threads,
)
from rag_access_guard_api.services.chat_state import ChatConflict
from rag_access_guard_api.services.chat_turns import load_turns
from rag_access_guard_api.services.errors import ForbiddenError
from rag_access_guard_api.services.security import PolicyUnitOfWork, revalidate_session
from rag_access_guard_api.services.tokens import matches_token


def build_chat_router(policy: PolicyUnitOfWork, settings: Settings) -> APIRouter:  # noqa: C901
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
        """Reauthorize each saved answer in one consistent policy snapshot."""
        check_origin(request, settings)
        if request.query_params:
            raise HTTPException(422, "Invalid request")
        session_token = cookies.credentials(request).session
        async with policy.protected_read(session_token) as uow:
            thread = await get_owned_thread(uow, thread_id, lock=True)
            await revalidate_session(uow, session_token)
            return ThreadDetail(
                id=thread.id,
                title=thread.title,
                revision=thread.revision,
                created_at=thread.created_at,
                turns=tuple(
                    [await read_turn(uow, turn) for turn in await load_turns(uow, thread_id)]
                ),
            )

    @router.post("/{thread_id}/messages")
    async def message(
        thread_id: UUID, payload: MessageRequest, request: Request
    ) -> MessageResponse:
        check_origin(request, settings, unsafe=True)
        if request.query_params:
            raise HTTPException(422, "Invalid request")
        credentials = cookies.credentials(request)
        result = await ChatService(policy, credentials.csrf).generate_turn(
            credentials.session, thread_id, payload
        )
        match result:
            case ChatConflict(reason=reason):
                raise HTTPException(409, reason)
            case MessageResponse():
                return result
            case _:
                assert_never(result)

    return router
