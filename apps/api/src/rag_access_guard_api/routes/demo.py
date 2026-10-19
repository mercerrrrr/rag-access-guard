"""Authenticated local guidance, independent of inference readiness."""

from fastapi import APIRouter, HTTPException, Request
from starlette.requests import ClientDisconnect

from rag_access_guard_api.config import Settings
from rag_access_guard_api.routes.auth import AuthCookies, check_origin
from rag_access_guard_api.schemas.demo import DemoProfile, SuggestionList
from rag_access_guard_api.services.chat_suggestions import (
    SuggestionsUnavailableError,
    list_suggestions,
)
from rag_access_guard_api.services.security import PolicyUnitOfWork, revalidate_session


async def _check_empty_request(request: Request) -> None:
    try:
        unexpected = request.query_params or await request.body()
    except ClientDisconnect:
        raise HTTPException(400, "Invalid request") from None
    if unexpected:
        raise HTTPException(422, "Invalid request")


def build_demo_router(policy: PolicyUnitOfWork, settings: Settings) -> APIRouter:
    """Bind guidance to the existing active-session and origin gates."""
    router = APIRouter(tags=["guidance"])
    cookies = AuthCookies(settings.loopback_development)

    @router.get("/api/chat/suggestions")
    async def suggestions(request: Request) -> SuggestionList:
        check_origin(request, settings)
        session = cookies.credentials(request).session
        async with policy.protected_read(session) as uow:
            await _check_empty_request(request)
            try:
                result = await list_suggestions(uow)
            except SuggestionsUnavailableError:
                raise HTTPException(503, "Guidance unavailable") from None
            await revalidate_session(uow, session)
            return result

    @router.get("/api/demo/profile")
    async def profile(request: Request) -> DemoProfile:
        check_origin(request, settings)
        async with policy.protected_read(cookies.credentials(request).session):
            await _check_empty_request(request)
            return DemoProfile()

    return router
