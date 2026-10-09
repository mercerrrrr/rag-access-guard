"""FastAPI application and health contracts."""

from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import ClassVar, Final, Literal

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, ConfigDict
from starlette.middleware.body_limit import RequestBodyLimitMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from rag_access_guard_api.adapters.inference_runtime import InferenceRuntime, bind_runtime
from rag_access_guard_api.adapters.llm import initialize_llm
from rag_access_guard_api.config import Settings
from rag_access_guard_api.database import create_database_engine, is_database_ready
from rag_access_guard_api.routes.access import build_access_router, build_role_router
from rag_access_guard_api.routes.audit import build_audit_router
from rag_access_guard_api.routes.auth import build_auth_router
from rag_access_guard_api.routes.auth_errors import register_auth_errors
from rag_access_guard_api.routes.chat import build_chat_router
from rag_access_guard_api.routes.documents import build_documents_router
from rag_access_guard_api.routes.search import build_search_router
from rag_access_guard_api.routes.sources import build_sources_router
from rag_access_guard_api.routes.users import build_users_router
from rag_access_guard_api.services.auth import AuthService
from rag_access_guard_api.services.security import PolicyUnitOfWork
from rag_access_guard_api.services.text_documents import MAX_UPLOAD_BYTES

SERVICE_UNAVAILABLE_DETAIL: Final = "Service unavailable"


class HealthResponse(BaseModel):
    """Successful health response."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    status: Literal["ok"] = "ok"


class InferenceScopeMiddleware:
    """Bind request inference without wrapping streaming receive exceptions."""

    def __init__(self, app: ASGIApp, get_runtime: Callable[[], InferenceRuntime | None]) -> None:
        """Resolve the lifespan-owned runtime when each request begins."""
        self._app: ASGIApp = app
        self._get_runtime: Callable[[], InferenceRuntime | None] = get_runtime

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Keep binding active through the original ASGI receive/send lifetime."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        with bind_runtime(self._get_runtime()):
            await self._app(scope, receive, send)


def create_app() -> FastAPI:
    """Create the FastAPI application."""
    settings = Settings()
    engine = create_database_engine(settings)
    auth = AuthService(engine, settings)
    runtime: InferenceRuntime | None = None

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        nonlocal runtime
        runtime = InferenceRuntime()
        try:
            with bind_runtime(runtime):
                await auth.initialize()
                await initialize_llm(settings)
                yield
        finally:
            await runtime.aclose()
            runtime = None
            await engine.dispose()

    application = FastAPI(title="RAG Access Guard API", lifespan=lifespan)

    application.add_middleware(InferenceScopeMiddleware, get_runtime=lambda: runtime)

    application.include_router(build_auth_router(auth, settings))
    application.include_router(build_chat_router(PolicyUnitOfWork(engine), settings))
    application.include_router(build_documents_router(engine, settings))
    application.include_router(build_search_router(engine, settings))
    application.include_router(build_access_router(PolicyUnitOfWork(engine), settings))
    application.include_router(build_sources_router(PolicyUnitOfWork(engine), settings))
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
