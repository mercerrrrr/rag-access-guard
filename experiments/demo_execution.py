"""Owned demo runtime admission and domain callback wiring for the shared loop."""

from dataclasses import replace
from functools import partial
from typing import assert_never

from anyio.to_thread import run_sync
from sqlalchemy.ext.asyncio import create_async_engine

from experiments.demo_admission import DemoAdmission
from experiments.demo_calibration import DemoCalibration
from experiments.demo_collection import DemoCollector
from experiments.demo_http import demo_client
from experiments.demo_inputs import DemoInputs
from experiments.demo_lifecycle import DemoExecution
from experiments.demo_run_contracts import DemoRunEnvelope, DemoUnit
from experiments.demo_sessions import DemoActorSessions
from rag_access_guard_api.demo.credentials import CredentialJournal
from rag_access_guard_api.demo.evaluation import DemoDatasetError
from rag_access_guard_api.demo.evaluation_admission import verify_owned_evaluation_target
from rag_access_guard_api.demo.files import load_private_json
from rag_access_guard_api.demo.private_write import owner_only
from rag_access_guard_api.schemas.evaluation_identity import EvaluationIdentity


async def admit_schedule(
    admission: DemoAdmission, sessions: DemoActorSessions, units: tuple[DemoUnit, ...]
) -> None:
    """Verify target ownership and every scheduled gold witness before the first question."""
    inputs = admission.inputs
    marker = await run_sync(verify_owned_evaluation_target, inputs.config)
    if marker != inputs.marker:
        raise DemoDatasetError(code="marker_mismatch")
    required = {u.gold.principal_login for u in units}
    for entry in sessions.journal.entries:
        if entry.login not in required:
            continue
        async with demo_client(inputs.config) as client:
            session = await sessions.authenticate(client, entry.login)
            for unit in units:
                if unit.gold.principal_login == entry.login:
                    await admission.check(session, unit.gold)


async def execute_demo(inputs: DemoInputs, execution: DemoExecution) -> DemoRunEnvelope:
    """Admit the exact owned database and frozen actor gold while holding the run-owner lock."""
    owner_only(inputs.config.credentials_path.parent)
    owner_only(inputs.config.credentials_path)
    journal = load_private_json(inputs.config.credentials_path, CredentialJournal)
    if journal.marker != inputs.marker or len({e.login for e in journal.entries}) != len(
        journal.entries
    ):
        raise DemoDatasetError(code="credential_identity_mismatch")
    required = {u.gold.principal_login for u in execution.plan.schedule}
    if not required <= {e.login for e in journal.entries}:
        raise DemoDatasetError(code="credential_actor_absent")
    engine = create_async_engine(
        inputs.config.database_url.get_secret_value(),
        hide_parameters=True,
        connect_args={"connect_timeout": 2},
        pool_size=2,
        max_overflow=0,
    )
    admission = DemoAdmission(inputs, engine)
    sessions = DemoActorSessions(journal)

    async def preflight() -> None:
        await admit_schedule(admission, sessions, execution.plan.schedule)

    identity = EvaluationIdentity(
        profile_hash=inputs.dataset.profile_hash,
        model_digest=execution.plan.model_digest,
        renderer_identity=execution.plan.renderer_identity,
        retrieval_config_hash=inputs.retrieval.config_sha256,
        corpus_sha256=inputs.dataset.corpus_sha256,
        policy_revision=inputs.dataset.policy_revision,
        demo_instance_id=inputs.marker.instance_id,
    )
    try:
        match execution.plan.mode:
            case "calibrate":
                callback = DemoCalibration(inputs, execution.plan, sessions, admission).score
                return await replace(execution, execute=callback, admission=preflight).run()
            case "holdout" | "rehearsal":
                collector = DemoCollector(
                    execution.plan,
                    identity,
                    sessions,
                    partial(demo_client, inputs.config),
                    admission.check,
                )
                return await replace(
                    execution, execute=collector.collect, admission=preflight
                ).run()
            case "validate":
                raise DemoDatasetError(code="validate_connection_forbidden")
            case _:
                assert_never(execution.plan.mode)
    finally:
        await engine.dispose()
