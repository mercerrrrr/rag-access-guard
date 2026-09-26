"""Fresh authorization for every server-issued source URL."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError

from rag_access_guard import SourceRef
from rag_access_guard_api.config import Settings
from rag_access_guard_api.routes.auth import AuthCookies, check_origin
from rag_access_guard_api.schemas.sources import SourceContent, SourceNotFound
from rag_access_guard_api.services.errors import UnauthenticatedError
from rag_access_guard_api.services.security import PolicyUnitOfWork, revalidate_session
from rag_access_guard_api.services.sources import read_source


def build_sources_router(policy: PolicyUnitOfWork, settings: Settings) -> APIRouter:
    """Expose GET only; HEAD cannot reveal resource-specific metadata."""
    router = APIRouter()
    cookies = AuthCookies(local=settings.loopback_development)

    @router.get("/api/documents/{document_id}/versions/{version_id}/content")
    async def content(
        request: Request, document_id: UUID, version_id: UUID, chunk_id: UUID
    ) -> SourceContent:
        check_origin(request, settings)
        token = cookies.credentials(request).session
        ref = SourceRef(document_id=document_id, document_version_id=version_id, chunk_id=chunk_id)
        try:
            async with policy.protected_read(token) as uow:
                result = await read_source(uow, ref)
                await revalidate_session(uow, token)
        except (UnauthenticatedError, SourceNotFound, SQLAlchemyError):
            raise HTTPException(status_code=404, detail="Not found") from None
        else:
            return result

    return router
