from datetime import timedelta
from functools import partial
from pathlib import Path
from typing import Literal, assert_never
from uuid import UUID, uuid4

import anyio
import httpx2
import pytest
from alembic import command
from alembic.config import Config
from experiments.demo_admission import DemoAdmission
from experiments.demo_calibration import DemoCalibration
from experiments.demo_collection import DemoCollector
from experiments.demo_execution import admit_schedule, execute_demo
from experiments.demo_inputs import DemoInputs
from experiments.demo_lifecycle import DemoExecution
from experiments.demo_run_contracts import DemoRecord, DemoUnit
from experiments.demo_run_storage import DemoRunStore
from experiments.demo_sessions import DemoActorSessions
from sqlalchemy import create_engine, func, insert, select, update
from sqlalchemy.ext.asyncio import create_async_engine

from experiments import demo_calibration, demo_execution
from rag_access_guard_api.adapters import embeddings
from rag_access_guard_api.demo.contracts import DemoConfig, DemoMarker
from rag_access_guard_api.demo.credentials import CredentialJournal
from rag_access_guard_api.demo.evaluation_contracts import DemoEvaluationCase
from rag_access_guard_api.demo.files import load_private_json
from rag_access_guard_api.main import create_app
from rag_access_guard_api.persistence import AuthRateBucket, ChatThread, ChatTurn, Session, User
from rag_access_guard_api.schemas.evaluation_identity import EvaluationIdentity
from rag_access_guard_api.server import create_event_loop
from rag_access_guard_api.services import query_validation
from rag_access_guard_api.services.passwords import hash_password
from rag_access_guard_api.services.security import PolicyUnitOfWork
from tests.integration.demo_session_fixtures import (
    SyntheticQueryCounter,
    session_clients,
    session_inputs,
    session_plan,
)
from tests.integration.embedding_fixtures import DeterministicEmbedder
from tests.integration.search_fixtures import configure_search


@pytest.fixture
def rate_limited_inputs(
    isolated_database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> DemoInputs:
    monkeypatch.setenv("RAG_ACCESS_GUARD_DATABASE_URL", isolated_database_url)
    monkeypatch.setenv("RAG_ACCESS_GUARD_AUTH_ORIGIN", "http://127.0.0.1:5179")
    monkeypatch.setenv("RAG_ACCESS_GUARD_LOOPBACK_DEVELOPMENT", "true")
    monkeypatch.setenv("RAG_ACCESS_GUARD_LLM_ADAPTER", "fake")
    configure_search(tmp_path / "search.json", monkeypatch)
    command.upgrade(Config("apps/api/alembic.ini"), "head")
    engine = create_engine(isolated_database_url, hide_parameters=True)
    try:
        with engine.begin() as connection:
            for login in ("demo.student", "demo.teacher", "demo.staff"):
                _ = connection.execute(
                    insert(User).values(
                        id=uuid4(),
                        login=login,
                        display_name="Synthetic",
                        password_hash=hash_password("synthetic-password"),
                    )
                )
    finally:
        engine.dispose()
    monkeypatch.setattr(embeddings, "get_embedding_adapter", DeterministicEmbedder)
    monkeypatch.setattr(demo_calibration, "get_embedding_adapter", DeterministicEmbedder)
    monkeypatch.setattr(query_validation, "get_tokenizer", SyntheticQueryCounter)
    inputs = session_inputs(tmp_path, isolated_database_url)

    def fixture_target(_config: DemoConfig) -> DemoMarker:
        return inputs.marker

    monkeypatch.setattr(demo_execution, "verify_owned_evaluation_target", fixture_target)

    async def authenticate_only(
        self: DemoAdmission, session: str, case: DemoEvaluationCase
    ) -> None:
        async with PolicyUnitOfWork(self.engine).protected_read(session) as uow:
            assert uow.user.login == case.principal_login

    async def authenticate_runtime(_self: DemoCollector, client: httpx2.AsyncClient) -> None:
        response = await client.get("/api/auth/me")
        _ = response.raise_for_status()

    monkeypatch.setattr(DemoAdmission, "check", authenticate_only)
    monkeypatch.setattr(DemoCollector, "verify_runtime", authenticate_runtime)
    return inputs


@pytest.mark.parametrize("mode", ["calibrate", "holdout"])
def test_preflight_and_six_same_actor_units_share_one_real_login(
    rate_limited_inputs: DemoInputs,
    monkeypatch: pytest.MonkeyPatch,
    mode: Literal["calibrate", "holdout"],
) -> None:
    # Given real unmodified auth limits and six same-account cases plus preflight.
    inputs = rate_limited_inputs
    plan = session_plan(inputs, mode)
    app = create_app()
    login_statuses: list[int] = []

    client = session_clients(app, login_statuses)

    monkeypatch.setattr(demo_execution, "demo_client", client)
    monkeypatch.setattr(demo_calibration, "demo_client", client)

    async def unreachable(_unit: DemoUnit, _run_id: UUID) -> DemoRecord:
        raise AssertionError

    async def run() -> None:
        # When the real execute_demo wiring performs preflight and every unit.
        async with app.router.lifespan_context(app):
            result = await execute_demo(inputs, DemoExecution(plan, unreachable))
        _, records = DemoRunStore(plan).load()
        # Then the actual rate-limited server accepts all six without repeated login.
        assert login_statuses == [200]
        assert result.status == "completed"
        assert len(records) == 6
        assert all(row.status == "success" for row in records)
        assert all(row.authentication_state == "reused" for row in records)
        assert all(row.authentication_elapsed_ms is None for row in records)
        engine = create_async_engine(
            inputs.config.database_url.get_secret_value(), hide_parameters=True
        )
        try:
            async with engine.connect() as connection:
                buckets = dict(
                    (await connection.execute(select(AuthRateBucket.kind, AuthRateBucket.attempts)))
                    .tuples()
                    .all()
                )
                assert buckets == {"bootstrap_ip": 1, "login_ip": 1, "login_account_ip": 1}
                if mode == "holdout":
                    assert (
                        await connection.execute(select(func.count()).select_from(ChatThread))
                    ).scalar_one() == 6
                    assert (
                        await connection.execute(
                            select(func.count(func.distinct(ChatTurn.request_id)))
                        )
                    ).scalar_one() == 6
        finally:
            await engine.dispose()

    anyio.run(run, backend_options={"loop_factory": create_event_loop})


@pytest.mark.parametrize("mode", ["calibrate", "holdout"])
@pytest.mark.parametrize("invalidation", ["logout", "expiry"])
def test_reused_session_invalidation_is_terminal_without_refresh(
    rate_limited_inputs: DemoInputs,
    monkeypatch: pytest.MonkeyPatch,
    mode: Literal["calibrate", "holdout"],
    invalidation: Literal["logout", "expiry"],
) -> None:
    # Given a genuine preflight session and one successful unit under actual auth limits.
    inputs = rate_limited_inputs
    plan = session_plan(inputs, mode)
    app = create_app()
    logins: list[int] = []
    client = session_clients(app, logins)
    monkeypatch.setattr(demo_execution, "demo_client", client)
    monkeypatch.setattr(demo_calibration, "demo_client", client)
    sessions = DemoActorSessions(
        load_private_json(inputs.config.credentials_path, CredentialJournal)
    )

    async def run() -> None:
        engine = create_async_engine(
            inputs.config.database_url.get_secret_value(), hide_parameters=True
        )
        admission = DemoAdmission(inputs, engine)
        identity = EvaluationIdentity(
            profile_hash=plan.profile_hash,
            model_digest=plan.model_digest,
            renderer_identity=plan.renderer_identity,
            retrieval_config_hash=plan.retrieval_config_hash,
            corpus_sha256=plan.corpus_sha256,
            policy_revision=0,
            demo_instance_id=inputs.marker.instance_id,
        )
        try:
            async with app.router.lifespan_context(app):
                await admit_schedule(admission, sessions, plan.schedule)
                match mode:
                    case "calibrate":
                        callback = DemoCalibration(inputs, plan, sessions, admission).score
                    case "holdout":
                        callback = DemoCollector(
                            plan,
                            identity,
                            sessions,
                            partial(client, inputs.config),
                            admission.check,
                        ).collect
                    case _:
                        assert_never(mode)
                first = await callback(plan.schedule[0], UUID(int=8000))
                assert first.status == "success"
                # When ordinary logout or real persisted expiry invalidates the cached token.
                match invalidation:
                    case "logout":
                        async with client(inputs.config) as revoker:
                            _ = sessions.bind(revoker, plan.schedule[0].gold.principal_login)
                            response = await revoker.post("/api/auth/logout")
                            assert response.status_code == 204
                    case "expiry":
                        async with engine.begin() as connection:
                            _ = await connection.execute(
                                update(Session).values(
                                    created_at=func.now() - timedelta(hours=9),
                                    last_seen_at=func.now() - timedelta(minutes=1),
                                    absolute_expires_at=func.now() - timedelta(seconds=1),
                                )
                            )
                    case _:
                        assert_never(invalidation)
                denied = await callback(plan.schedule[1], UUID(int=8000))
                # Then the next unit is a failed null observation, not a refreshed login/question.
                assert denied.status == "error"
                assert denied.error_code == "actor_session_invalid"
                assert denied.model_digest is None
                assert denied.elapsed_ms is not None
                assert logins == [200]
                async with engine.connect() as connection:
                    assert (
                        await connection.execute(
                            select(AuthRateBucket.attempts).where(
                                AuthRateBucket.kind == "login_account_ip"
                            )
                        )
                    ).scalar_one() == 1
                    expected = 1 if mode == "holdout" else 0
                    assert (
                        await connection.execute(select(func.count()).select_from(ChatThread))
                    ).scalar_one() == expected
        finally:
            await engine.dispose()

    anyio.run(run, backend_options={"loop_factory": create_event_loop})
