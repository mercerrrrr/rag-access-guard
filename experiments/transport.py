"""Private experiment HTTP surface, kept separate from the production application."""

from dataclasses import replace
from uuid import UUID

import httpx2 as httpx
from fastapi import FastAPI
from starlette.responses import JSONResponse

from experiments.host import ExperimentHost
from experiments.observations import Observation
from experiments.scenario_types import FrozenModel, Principal
from rag_access_guard import SourceRef
from rag_access_guard_api.routes.auth_errors import register_auth_errors
from rag_access_guard_api.schemas.access import AccessibleDocuments
from rag_access_guard_api.schemas.chat import MessageResponse, ThreadDetail
from rag_access_guard_api.schemas.sources import SourceContent


class Question(FrozenModel):
    """Experiment action identity plus the unchanged question sent in both arms."""

    action_id: str
    user_input: str


def create_transport(host: ExperimentHost) -> httpx.ASGITransport:
    """Exercise real HTTP serialization after transactions commit, without a public route."""
    app = FastAPI()
    register_auth_errors(app)

    @app.post("/api/chat/messages")
    async def ask(payload: Question) -> MessageResponse:
        return await host.ask(payload.action_id, payload.user_input)

    @app.get("/api/chat/thread")
    async def thread(action_id: str, reader: Principal) -> ThreadDetail:
        return await host.stored_read(action_id, reader)

    @app.get("/api/documents")
    async def documents(action_id: str) -> AccessibleDocuments:
        return await host.document_list(action_id)

    @app.get("/api/documents/source", response_model=None)
    async def source(
        action_id: str,
        document_id: UUID,
        version_id: UUID,
        chunk_id: UUID,
    ) -> SourceContent | JSONResponse:
        result = await host.source_read(
            action_id,
            SourceRef(
                document_id=document_id,
                document_version_id=version_id,
                chunk_id=chunk_id,
            ),
        )
        if result is None:
            return JSONResponse({"detail": "Not found"}, status_code=404)
        return result

    return httpx.ASGITransport(app=app)


def capture_http(host: ExperimentHost, action_id: str, response: httpx.Response | None) -> None:
    """Attach observed transport status, bytes and cache policy to the service evidence."""
    if response is None:
        return
    if not any(
        item.action_id == action_id and item.surface != "model_context"
        for item in host.observations
    ):
        path = response.request.url.path
        host.observations.append(
            Observation(
                action_id=action_id,
                surface="stored_read"
                if path == "/api/chat/thread"
                else "source_read"
                if path == "/api/documents/source"
                else "document_list"
                if path == "/api/documents"
                else "release",
                source_refs=(),
                forbidden_refs=(),
                provenance_valid=True,
                elapsed_ms=response.elapsed.total_seconds() * 1000,
            )
        )
    for index, observation in enumerate(host.observations):
        if observation.action_id == action_id and observation.surface != "model_context":
            host.observations[index] = replace(
                observation,
                http_status=response.status_code,
                cache_control=response.headers.get("cache-control", ""),
                response_body=response.text,
                http_elapsed_ms=response.elapsed.total_seconds() * 1000,
            )
