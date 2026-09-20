"""FastAPI application and process health contract."""

from typing import ClassVar, Literal

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict


class LiveHealthResponse(BaseModel):
    """Process liveness response."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    status: Literal["ok"] = "ok"


app = FastAPI(title="RAG Access Guard API")


@app.get(
    "/api/health/live",
    summary="Report API liveness",
    tags=["health"],
)
async def read_live_health() -> LiveHealthResponse:
    """Report that the API process can serve requests."""
    return LiveHealthResponse()
