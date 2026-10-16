"""Authenticate each readiness read, with probes outside the policy transaction."""

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from rag_access_guard_api.config import Settings
from rag_access_guard_api.persistence import DemoSeedState
from rag_access_guard_api.routes.auth import AuthCookies, check_origin
from rag_access_guard_api.schemas.evaluation_identity import EvaluationIdentity
from rag_access_guard_api.schemas.generation import InferenceUnavailableError
from rag_access_guard_api.schemas.search import RetrievalNotConfiguredError
from rag_access_guard_api.schemas.system import SystemStatus
from rag_access_guard_api.services.evaluation_identity import evaluation_metadata
from rag_access_guard_api.services.security import PolicyUnitOfWork
from rag_access_guard_api.services.system_status import SystemReadiness


def build_system_router(policy: PolicyUnitOfWork, settings: Settings) -> APIRouter:
    """Retain a readiness cache, never a grant or session cache."""
    router = APIRouter(prefix="/api/system", tags=["system"])
    cookies = AuthCookies(settings.loopback_development)
    readiness = SystemReadiness(settings)

    @router.get("/status")
    async def status(request: Request) -> SystemStatus:
        check_origin(request, settings)
        credentials = cookies.credentials(request)
        async with policy.protected_read(credentials.session):
            pass
        if request.query_params or await request.body():
            raise HTTPException(422, "Invalid request")
        result = await readiness.snapshot()
        async with policy.protected_read(credentials.session):
            return result

    @router.get("/evaluation-identity")
    async def identity(request: Request) -> EvaluationIdentity:
        check_origin(request, settings)
        credentials = cookies.credentials(request)
        async with policy.protected_read(credentials.session):
            pass
        if request.query_params or await request.body():
            raise HTTPException(422, "Invalid request")
        try:
            metadata = evaluation_metadata(settings, await readiness.snapshot())
        except (InferenceUnavailableError, RetrievalNotConfiguredError):
            metadata = None
        async with policy.protected_read(credentials.session) as uow:
            if metadata is None:
                raise HTTPException(503, "Evaluation identity unavailable")
            instance = (
                await uow.connection.execute(select(DemoSeedState.instance_id))
            ).scalar_one_or_none()
            return EvaluationIdentity(
                profile_hash=metadata.profile_hash,
                model_digest=metadata.model_digest,
                renderer_identity=metadata.renderer_identity,
                retrieval_config_hash=metadata.retrieval_config_hash,
                corpus_sha256=metadata.corpus_sha256,
                policy_revision=uow.revision,
                demo_instance_id=instance,
            )

    return router
