"""Actor-aware HTTP collection; separate search observations remain explicitly labeled."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from http import HTTPStatus
from time import perf_counter
from typing import assert_never
from uuid import UUID

import anyio
import httpx2
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from experiments.demo_run_contracts import DemoRunPlan, DemoUnit
from experiments.demo_sessions import DemoActorSessions
from rag_access_guard_api.demo.evaluation import DemoDatasetError
from rag_access_guard_api.demo.evaluation_contracts import (
    DemoCaseResult,
    DemoEvaluationCase,
    DemoSourceRef,
)
from rag_access_guard_api.schemas.chat import (
    AvailableTurn,
    MessageRequest,
    MessageResponse,
    NeutralTurn,
    PendingTurn,
    ThreadDetail,
    ThreadView,
    UnavailableTurn,
)
from rag_access_guard_api.schemas.evaluation_identity import EvaluationIdentity
from rag_access_guard_api.schemas.search import SearchResponse
from rag_access_guard_api.schemas.system import SystemStatus
from rag_access_guard_api.services.errors import UnauthenticatedError


@dataclass(frozen=True, slots=True)
class DemoCollector:
    """Reuse only preflight authentication; each case owns a fresh client and empty chat."""

    plan: DemoRunPlan
    expected_identity: EvaluationIdentity
    sessions: DemoActorSessions
    client_factory: Callable[[], httpx2.AsyncClient]
    admit: Callable[[str, DemoEvaluationCase], Awaitable[None]]

    async def verify_runtime(self, client: httpx2.AsyncClient) -> None:
        """Fail closed before a question if mode, profile, config, policy or instance drifts."""
        status_response = await client.get("/api/system/status")
        _ = status_response.raise_for_status()
        status = SystemStatus.model_validate_json(status_response.content)
        if (
            status.mode != "local"
            or status.model.state != "ready"
            or status.search.state != "ready"
        ):
            raise DemoDatasetError(code="runtime_not_real_ready")
        response = await client.get("/api/system/evaluation-identity")
        _ = response.raise_for_status()
        if EvaluationIdentity.model_validate_json(response.content) != self.expected_identity:
            raise DemoDatasetError(code="runtime_identity_drift")

    async def collect(self, unit: DemoUnit, run_id: UUID) -> DemoCaseResult:
        """Post-auth elapsed includes all checks/search/chat; preflight login is outside it."""
        started = perf_counter()
        base = DemoCaseResult(
            run_id=run_id,
            unit_id=unit.unit_id,
            case_id=unit.case_id,
            round_number=unit.round_number,
            warmup=unit.warmup,
            principal_login=unit.gold.principal_login,
            request_id=unit.request_id,
            status="error",
            elapsed_ms=None,
            phase_timings=None,
            retrieved_refs=None,
            released_refs=None,
            answer=None,
            model_digest=None,
            profile_hash=self.plan.profile_hash,
            config_hash=self.plan.retrieval_config_hash,
            policy_revision=None,
            error_code="unavailable",
            telemetry_reason="phases_unknown; separate_actor_search; preflight_auth_unmeasured",
            gold=unit.gold,
            dataset_sha256=self.plan.dataset_sha256,
        )
        try:
            with anyio.fail_after(150):
                async with self.client_factory() as client:
                    session = self.sessions.bind(client, unit.gold.principal_login)
                    base = base.model_copy(update={"authentication_state": "reused"})
                    started = perf_counter()
                    await self.verify_runtime(client)
                    await self.admit(session, unit.gold)
                    observed = await self._question(client, unit)
                    base = base.model_copy(
                        update={
                            "retrieved_refs": observed.retrieved_refs,
                            "chat_response_elapsed_ms": observed.chat_elapsed_ms,
                            "retrieval_observation": "separate_actor_search",
                        }
                    )
                    await self.verify_runtime(client)
                    terminal = base.model_copy(
                        update={
                            "elapsed_ms": (perf_counter() - started) * 1000,
                            "model_digest": self.expected_identity.model_digest,
                            "policy_revision": self.expected_identity.policy_revision,
                        }
                    )
                    match observed:
                        case CaseObservation():
                            terminal = terminal.model_copy(
                                update={
                                    "status": "success",
                                    "released_refs": observed.released_refs,
                                    "answer": observed.answer,
                                    "error_code": None,
                                }
                            )
                        case _ClosedCaseObservation():
                            terminal = terminal.model_copy(
                                update={"error_code": "closed_generation_failure"}
                            )
                        case _:
                            assert_never(observed)
                    return terminal
        except (TimeoutError, httpx2.TimeoutException):
            return base.model_copy(
                update={
                    "status": "timeout",
                    "error_code": "timeout",
                    "elapsed_ms": (perf_counter() - started) * 1000,
                }
            )
        except UnauthenticatedError:
            return base.model_copy(
                update={
                    "error_code": "actor_session_invalid",
                    "elapsed_ms": (perf_counter() - started) * 1000,
                }
            )
        except httpx2.HTTPStatusError as error:
            return base.model_copy(
                update={
                    "error_code": "actor_session_invalid"
                    if error.response.status_code == HTTPStatus.UNAUTHORIZED
                    else "transport_or_response_failed",
                    "elapsed_ms": (perf_counter() - started) * 1000,
                }
            )
        except DemoDatasetError as error:
            return base.model_copy(
                update={"error_code": error.code, "elapsed_ms": (perf_counter() - started) * 1000}
            )
        except (httpx2.HTTPError, ValidationError, SQLAlchemyError):
            return base.model_copy(
                update={
                    "error_code": "transport_or_response_failed",
                    "elapsed_ms": (perf_counter() - started) * 1000,
                }
            )

    async def _question(
        self, client: httpx2.AsyncClient, unit: DemoUnit
    ) -> "CaseObservation | _ClosedCaseObservation":
        if unit.request_id is None:
            raise DemoDatasetError(code="request_identity_absent")
        response = await client.post("/api/search", json={"query": unit.gold.question, "limit": 5})
        _ = response.raise_for_status()
        search = SearchResponse.model_validate_json(response.content)
        created = await client.post("/api/chat/threads", json={})
        _ = created.raise_for_status()
        thread = ThreadView.model_validate_json(created.content)
        detail = await client.get(f"/api/chat/threads/{thread.id}")
        _ = detail.raise_for_status()
        if thread.revision != 0 or ThreadDetail.model_validate_json(detail.content).turns:
            raise DemoDatasetError(code="chat_not_empty")
        request = MessageRequest(
            request_id=unit.request_id, expected_thread_revision=0, user_input=unit.gold.question
        )
        sent = perf_counter()
        completed = await client.post(
            f"/api/chat/threads/{thread.id}/messages",
            content=request.model_dump_json(),
            headers={"Content-Type": "application/json"},
        )
        elapsed = (perf_counter() - sent) * 1000
        _ = completed.raise_for_status()
        result = MessageResponse.model_validate_json(completed.content)
        if result.replayed or result.turn.request_id != unit.request_id:
            raise DemoDatasetError(code="request_replayed_or_mismatched")
        retrieved = tuple(
            DemoSourceRef(
                document_id=r.source_ref.document_id,
                version_id=r.source_ref.document_version_id,
                chunk_id=r.source_ref.chunk_id,
            )
            for r in search.items
        )
        match result.turn:
            case AvailableTurn() as turn:
                released = tuple(
                    DemoSourceRef(
                        document_id=r.document_id,
                        version_id=r.document_version_id,
                        chunk_id=r.chunk_id,
                    )
                    for r in turn.sources
                )
                return CaseObservation(retrieved, released, turn.answer, elapsed)
            case NeutralTurn() as turn:
                if turn.message != "Нет доступных источников для ответа.":
                    return _ClosedCaseObservation(retrieved, elapsed)
                return CaseObservation(retrieved, (), turn.message, elapsed)
            case PendingTurn() | UnavailableTurn():
                raise DemoDatasetError(code="incomplete_chat_response")
            case _:
                assert_never(result.turn)


@dataclass(frozen=True, slots=True)
class CaseObservation:
    """Only authorized public HTTP fields and measured complete-body duration."""

    retrieved_refs: tuple[DemoSourceRef, ...]
    released_refs: tuple[DemoSourceRef, ...]
    answer: str
    chat_elapsed_ms: float


@dataclass(frozen=True, slots=True)
class _ClosedCaseObservation:
    retrieved_refs: tuple[DemoSourceRef, ...]
    chat_elapsed_ms: float
