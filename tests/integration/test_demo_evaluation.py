from hashlib import sha256

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, update
from sqlalchemy.ext.asyncio import create_async_engine

from rag_access_guard_api.adapters.canonical import CanonicalChunk, canonical_query
from rag_access_guard_api.config import Settings
from rag_access_guard_api.demo.evaluation import DemoDatasetError
from rag_access_guard_api.demo.evaluation_admission import validate_case_evidence
from rag_access_guard_api.demo.evaluation_contracts import DemoEvidence, DemoEvidenceSet
from rag_access_guard_api.persistence import Session
from rag_access_guard_api.schemas.access import GrantView
from rag_access_guard_api.schemas.documents import DocumentSummary
from rag_access_guard_api.schemas.system import ModelStatus, SearchStatus, SystemStatus
from rag_access_guard_api.server import create_event_loop
from rag_access_guard_api.services.security import PolicyUnitOfWork
from rag_access_guard_api.services.system_status import SystemReadiness
from tests.unit.demo_evaluation_fixtures import dataset


def test_evaluation_identity_requires_session(auth_client: TestClient) -> None:
    response = auth_client.get("/api/system/evaluation-identity")
    assert response.status_code == 401
    assert response.headers["cache-control"] == "private, no-store"


def test_evaluation_identity_refuses_unverified_runtime(authenticated_client: TestClient) -> None:
    response = authenticated_client.get("/api/system/evaluation-identity")
    assert response.status_code == 503
    assert response.headers["cache-control"] == "private, no-store"


def test_evaluation_identity_reauthenticates_after_failed_probe(
    authenticated_client: TestClient,
    auth_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def revoked(_self: SystemReadiness) -> SystemStatus:
        with auth_database.begin() as connection:
            _ = connection.execute(update(Session).values(revoked_at=func.now()))
        return SystemStatus(
            mode="disabled",
            model=ModelStatus(name=None, state="disabled"),
            search=SearchStatus(state="unavailable"),
        )

    monkeypatch.setattr(SystemReadiness, "snapshot", revoked)
    response = authenticated_client.get("/api/system/evaluation-identity")
    assert response.status_code == 401


@pytest.mark.parametrize("suffix", ["?profile=other", "?model=other", "?threshold=0"])
def test_evaluation_identity_rejects_selectors(
    authenticated_client: TestClient,
    suffix: str,
) -> None:
    response = authenticated_client.get("/api/system/evaluation-identity" + suffix)
    assert response.status_code == 422


def test_evidence_admission_checks_real_span_and_actor_grant(
    admin_client: TestClient,
    registered_document: DocumentSummary,
    auth_database: Engine,
    self_grant: GrantView,
) -> None:

    with auth_database.connect() as connection:
        row = CanonicalChunk.model_validate(connection.execute(canonical_query()).mappings().one())
    text = row.text
    evidence = DemoEvidence(
        id="witness",
        document_id=registered_document.id,
        version_id=row.document_version_id,
        chunk_id=row.chunk_id,
        span_start=0,
        span_end=len(text),
        supporting_text=text,
        supporting_sha256=sha256(text.encode()).hexdigest(),
        fact_ids=("fact",),
    )
    case = (
        dataset()
        .cases[0]
        .model_copy(
            update={
                "principal_login": "reader",
                "allowed_version_ids": (registered_document.active_version_id,),
                "evidence_sets": (DemoEvidenceSet(id="sufficient", evidence=(evidence,)),),
            }
        )
    )

    async def admitted() -> None:
        engine = create_async_engine(str(Settings().database_url))
        try:
            policy = PolicyUnitOfWork(engine)
            async with policy.protected_read(admin_client.cookies["__Host-rag_session"]) as uow:
                await validate_case_evidence(uow, case)
            changed = case.model_copy(
                update={
                    "evidence_sets": (
                        DemoEvidenceSet(
                            id="altered",
                            evidence=(
                                evidence.model_copy(
                                    update={
                                        "supporting_text": "altered",
                                        "supporting_sha256": sha256(b"altered").hexdigest(),
                                    }
                                ),
                            ),
                        ),
                    )
                }
            )
            async with policy.protected_read(admin_client.cookies["__Host-rag_session"]) as uow:
                with pytest.raises(DemoDatasetError, match="canonical_evidence_mismatch"):
                    await validate_case_evidence(uow, changed)
        finally:
            await engine.dispose()

    anyio.run(admitted, backend_options={"loop_factory": create_event_loop})
    response = admin_client.delete(
        f"/api/admin/documents/{registered_document.id}/grants/{self_grant.id}",
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert response.status_code == 204

    async def denied() -> None:
        engine = create_async_engine(str(Settings().database_url))
        try:
            async with PolicyUnitOfWork(engine).protected_read(
                admin_client.cookies["__Host-rag_session"]
            ) as uow:
                with pytest.raises(DemoDatasetError, match="inaccessible_or_stale_evidence"):
                    await validate_case_evidence(uow, case)
        finally:
            await engine.dispose()

    anyio.run(denied, backend_options={"loop_factory": create_event_loop})
