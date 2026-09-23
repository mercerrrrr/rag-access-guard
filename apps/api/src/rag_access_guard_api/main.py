"""FastAPI application and health contracts."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import ClassVar, Final, Literal

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, ConfigDict
from starlette.middleware.body_limit import RequestBodyLimitMiddleware

from rag_access_guard_api.config import Settings
from rag_access_guard_api.database import create_database_engine, is_database_ready
from rag_access_guard_api.routes.access import build_access_router, build_role_router
from rag_access_guard_api.routes.audit import build_audit_router
from rag_access_guard_api.routes.auth import build_auth_router
from rag_access_guard_api.routes.auth_errors import register_auth_errors
from rag_access_guard_api.routes.documents import build_documents_router
from rag_access_guard_api.routes.users import build_users_router
from rag_access_guard_api.services.auth import AuthService
from rag_access_guard_api.services.security import PolicyUnitOfWork
from rag_access_guard_api.services.text_documents import MAX_UPLOAD_BYTES

SERVICE_UNAVAILABLE_DETAIL: Final = "Service unavailable"


class HealthResponse(BaseModel):
    """Successful health response."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    status: Literal["ok"] = "ok"


def create_app() -> FastAPI:
    """Create the FastAPI application."""
    settings = Settings()
    engine = create_database_engine(settings)
    auth = AuthService(engine, settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        try:
            await auth.initialize()
            yield
        finally:
            await engine.dispose()

    application = FastAPI(title="RAG Access Guard API", lifespan=lifespan)

    application.include_router(build_auth_router(auth, settings))
    application.include_router(build_documents_router(PolicyUnitOfWork(engine), settings))
    application.include_router(build_access_router(PolicyUnitOfWork(engine), settings))
    application.include_router(build_role_router(PolicyUnitOfWork(engine), settings))
    application.include_router(build_users_router(PolicyUnitOfWork(engine), settings))
    application.include_router(build_audit_router(PolicyUnitOfWork(engine), settings))
    application.add_middleware(RequestBodyLimitMiddleware, max_body_size=MAX_UPLOAD_BYTES)
    register_auth_errors(application)

    @application.get(
        "/api/health/live",
        summary="Report API liveness",
        tags=["health"],
    )
    async def read_live_health() -> HealthResponse:
        """Report that the API process can serve requests."""
        return HealthResponse()

    @application.get(
        "/api/health/ready",
        summary="Report database readiness",
        tags=["health"],
    )
    async def read_ready_health() -> HealthResponse:
        """Report that the required database baseline is available."""
        if not await is_database_ready(engine):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=SERVICE_UNAVAILABLE_DETAIL,
            )
        return HealthResponse()

    return application


app = create_app()
