"""FastAPI application and health contracts."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import ClassVar, Final, Literal

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, ConfigDict

from rag_access_guard_api.config import Settings
from rag_access_guard_api.database import create_database_engine, is_database_ready

SERVICE_UNAVAILABLE_DETAIL: Final = "Service unavailable"


class HealthResponse(BaseModel):
    """Successful health response."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    status: Literal["ok"] = "ok"


def create_app() -> FastAPI:
    """Create the FastAPI application."""
    engine = create_database_engine(Settings())

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        yield
        await engine.dispose()

    application = FastAPI(title="RAG Access Guard API", lifespan=lifespan)

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
