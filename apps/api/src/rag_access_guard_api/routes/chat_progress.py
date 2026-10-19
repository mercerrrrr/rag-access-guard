"""An owned-request metadata read with no answer or source loading."""

from datetime import UTC
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from rag_access_guard_api.config import Settings
from rag_access_guard_api.routes.auth import AuthCookies, check_origin
from rag_access_guard_api.schemas.chat_progress import RequestStatus
from rag_access_guard_api.services.chat_progress import (
    RequestProgressTracker,
    project_request_status,
)
from rag_access_guard_api.services.chat_repository import get_owned_thread
from rag_access_guard_api.services.chat_turns import load_request_metadata
from rag_access_guard_api.services.security import PolicyUnitOfWork, database_clock


def build_chat_progress_router(
    policy: PolicyUnitOfWork, settings: Settings, progress: RequestProgressTracker
) -> APIRouter:
    """Reuse the chat session gate with the status-specific browser origin proof."""
    router = APIRouter()
    cookies = AuthCookies(settings.loopback_development)

    @router.get("/{thread_id}/requests/{request_id}/status")
    async def request_status(thread_id: UUID, request_id: UUID, request: Request) -> RequestStatus:
        """Prove ownership before metadata or memory, then project the database snapshot."""
        check_origin(request, settings)
        if (
            request.headers.get("origin") is None
            and request.headers.get("sec-fetch-site") != "same-origin"
        ):
            raise HTTPException(403, "Forbidden")
        if request.query_params:
            raise HTTPException(422, "Invalid request")
        async with policy.protected_read(cookies.credentials(request).session) as uow:
            _ = await get_owned_thread(uow, thread_id)
            metadata = await load_request_metadata(uow, thread_id, request_id)
            now = (await database_clock(uow.connection)).astimezone(UTC)
        return project_request_status(
            request_id,
            state=metadata.state if metadata is not None else None,
            lease_expires_at=metadata.lease_expires_at if metadata is not None else None,
            created_at=metadata.created_at if metadata is not None else None,
            now=now,
            stage=progress.get_stage(thread_id, request_id),
        )

    return router
