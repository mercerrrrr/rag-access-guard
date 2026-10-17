"""Actor-aware calibration adapter and private threshold selection artifact."""

from dataclasses import dataclass
from time import perf_counter
from typing import Final, assert_never
from uuid import UUID

import httpx2
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from experiments.demo_admission import DemoAdmission
from experiments.demo_calibration_scoring import (
    score_case_thresholds,
    select_case_threshold,
    select_recall_threshold,
)
from experiments.demo_http import demo_client
from experiments.demo_inputs import DemoInputs, calibration_cases_hash
from experiments.demo_run_contracts import DemoRunPlan, DemoUnit
from experiments.demo_sessions import DemoActorSessions
from rag_access_guard_api.adapters.embeddings import MODEL_ID, get_embedding_adapter
from rag_access_guard_api.adapters.tokenizer import MODEL_REVISION, TOKENIZER_IDENTITY
from rag_access_guard_api.demo.calibration import actor_top_five
from rag_access_guard_api.demo.calibration_contracts import (
    DemoCalibrationObjective,
    DemoCalibrationResult,
    DemoCaseCalibrationReport,
    DemoChunkScore,
    DemoRecallCalibrationReport,
)
from rag_access_guard_api.demo.evaluation import DemoDatasetError
from rag_access_guard_api.schemas.embedding_vectors import EmbeddingError
from rag_access_guard_api.schemas.retrieval_config import RetrievalConfig
from rag_access_guard_api.services.chunking import CHUNKER_REVISION
from rag_access_guard_api.services.errors import UnauthenticatedError
from rag_access_guard_api.services.security import PolicyUnitOfWork

CALIBRATION_COUNT: Final = 20


@dataclass(frozen=True, slots=True)
class DemoCalibration:
    """Only scheduled calibration cases can reach threshold selection."""

    inputs: DemoInputs
    plan: DemoRunPlan
    sessions: DemoActorSessions
    admission: DemoAdmission

    async def score(self, unit: DemoUnit, run_id: UUID) -> DemoCalibrationResult:
        """Embed outside locks, then score only current visible production-order top five."""
        started = perf_counter()
        base = DemoCalibrationResult(
            run_id=run_id,
            unit_id=unit.unit_id,
            case_id=unit.case_id,
            principal_login=unit.gold.principal_login,
            status="error",
            elapsed_ms=None,
            scores=None,
            relevant=(),
            model_digest=None,
            profile_hash=self.plan.profile_hash,
            config_hash=self.plan.retrieval_config_hash,
            policy_revision=None,
            error_code="unavailable",
            gold=unit.gold,
            dataset_sha256=self.plan.dataset_sha256,
        )
        try:
            async with demo_client(self.inputs.config) as client:
                session = self.sessions.bind(client, unit.gold.principal_login)
                base = base.model_copy(update={"authentication_state": "reused"})
                scores = await self._scores(unit, session)
            relevant = tuple(
                sorted(
                    {
                        e.chunk_id
                        for alternative in unit.gold.evidence_sets
                        for e in alternative.evidence
                    }
                )
            )
            return base.model_copy(
                update={
                    "status": "success",
                    "elapsed_ms": (perf_counter() - started) * 1000,
                    "scores": scores,
                    "relevant": relevant,
                    "policy_revision": self.inputs.dataset.policy_revision,
                    "model_digest": self.plan.model_digest,
                    "error_code": None,
                }
            )
        except UnauthenticatedError:
            return base.model_copy(
                update={
                    "error_code": "actor_session_invalid",
                    "elapsed_ms": (perf_counter() - started) * 1000,
                }
            )
        except DemoDatasetError as error:
            return base.model_copy(
                update={"error_code": error.code, "elapsed_ms": (perf_counter() - started) * 1000}
            )
        except (httpx2.HTTPError, SQLAlchemyError, EmbeddingError, ValidationError):
            return base.model_copy(
                update={
                    "error_code": "calibration_failed",
                    "elapsed_ms": (perf_counter() - started) * 1000,
                }
            )

    async def _scores(self, unit: DemoUnit, session: str) -> tuple[DemoChunkScore, ...]:
        if unit.gold.split != "calibration":
            raise DemoDatasetError(code="holdout_calibration_forbidden")
        await self.admission.check(session, unit.gold)
        embedder = get_embedding_adapter()
        if embedder.model_id != MODEL_ID or embedder.revision != MODEL_REVISION:
            raise DemoDatasetError(code="embedding_identity_mismatch")
        vector = await embedder.embed_query(unit.gold.question)
        await self.admission.check(session, unit.gold)
        async with PolicyUnitOfWork(self.admission.engine).protected_read(session) as uow:
            if uow.revision != self.inputs.dataset.policy_revision:
                raise DemoDatasetError(code="policy_revision_drift")
            scores = await actor_top_five(uow, vector)
        return tuple(DemoChunkScore(chunk_id=row.chunk_id, score=row.score) for row in scores)


def calibration_report(
    inputs: DemoInputs,
    rows: tuple[DemoCalibrationResult, ...],
    *,
    objective: DemoCalibrationObjective = "answerable-recall-top5-v1",
) -> DemoCaseCalibrationReport | DemoRecallCalibrationReport:
    """Persist grid and computed RetrievalConfig privately, never change the selected default."""
    cases = {c.id: c for c in inputs.dataset.cases if c.split == "calibration"}
    if (
        len(rows) != CALIBRATION_COUNT
        or len(cases) != CALIBRATION_COUNT
        or {r.case_id for r in rows} != cases.keys()
        or any(
            r.status != "success" or r.scores is None or r.gold != cases.get(r.case_id)
            for r in rows
        )
    ):
        raise DemoDatasetError(code="incomplete_calibration")
    grid = score_case_thresholds(rows)
    match objective:
        case "case-balanced-adequacy-top5-v1":
            chosen = select_case_threshold(grid)
            report_model = DemoCaseCalibrationReport
        case "answerable-recall-top5-v1":
            chosen = select_recall_threshold(grid)
            report_model = DemoRecallCalibrationReport
        case _:
            assert_never(objective)
    config = RetrievalConfig(
        threshold=chosen.threshold,
        no_results=chosen.threshold > 1,
        dataset_sha256=calibration_cases_hash(inputs.dataset),
        corpus_sha256=inputs.dataset.corpus_sha256,
        model_id=MODEL_ID,
        model_revision=MODEL_REVISION,
        tokenizer_revision=TOKENIZER_IDENTITY,
        chunker_revision=CHUNKER_REVISION,
        scorer_revision=objective,
        similarity_revision="pgvector-0.8.6-vector-cosine-v1",
        config_sha256="0" * 64,
    )
    config = config.model_copy(update={"config_sha256": config.computed_hash()})
    config.require_compatible()
    return report_model(
        dataset_sha256=inputs.dataset.dataset_sha256,
        calibration_cases_sha256=calibration_cases_hash(inputs.dataset),
        corpus_sha256=inputs.dataset.corpus_sha256,
        config=config,
        chosen=chosen,
        grid=grid,
    )
