import asyncio
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import httpx2 as httpx
import pytest
from experiments.comparison import ComparisonConfig
from experiments.harness import ScenarioHarness
from experiments.host import ContextModel, ExperimentHost
from experiments.observations import Arm
from experiments.security_scenarios import load_scenarios
from experiments.transport import create_transport
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from rag_access_guard_api.persistence import ChatTurn, TurnSource
from rag_access_guard_api.schemas.chat import MessageResponse, NeutralTurn
from rag_access_guard_api.server import create_event_loop
from rag_access_guard_api.services.security import PolicyUnitOfWork, database_clock


async def alter_attempt(engine: AsyncEngine, fault: str) -> None:
    async with engine.begin() as connection:
        statement = update(ChatTurn).where(ChatTurn.state == "pending")
        if fault == "lease":
            statement = statement.values(
                lease_expires_at=await database_clock(connection) - timedelta(seconds=1)
            )
        else:
            statement = statement.values(request_sha256=b"0" * 32)
        _ = await connection.execute(statement)


@pytest.mark.parametrize("arm", ["baseline", "guarded"])
@pytest.mark.parametrize("fault", ["csrf", "lease", "session", "request"])
def test_host_integrity_is_not_disabled_by_baseline(
    arm: Arm,
    fault: str,
    comparison_harness: ScenarioHarness,
    comparison_config: ComparisonConfig,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def run() -> None:
        case = next(
            c for c in load_scenarios(Path("experiments/scenarios.json")) if c.id == "allowed-staff"
        )
        async with (
            comparison_harness.open_pair(case, comparison_config) as pair,
            pair.databases.database(template=pair.template) as (_, url),
        ):
            engine = create_async_engine(url, poolclass=NullPool, hide_parameters=True)
            try:
                host = ExperimentHost(
                    PolicyUnitOfWork(engine),
                    pair.corpus,
                    pair.corpus.actors[case.principal_key],
                    comparison_config,
                    arm,
                )
                if fault == "csrf":
                    host.actor = replace(host.actor, csrf="invalid")
                original = ContextModel.generate

                async def generate(
                    self: ContextModel, *, user_input: str, system_supplied_context: str
                ) -> str:
                    body = await original(
                        self,
                        user_input=user_input,
                        system_supplied_context=system_supplied_context,
                    )
                    if fault == "session":
                        host.actor = pair.corpus.actors["teacher"]
                    else:
                        await alter_attempt(engine, fault)
                    return body

                with pytest.MonkeyPatch.context() as patch:
                    patch.setattr(ContextModel, "generate", generate)
                    async with httpx.AsyncClient(
                        transport=create_transport(host),
                        base_url="http://experiment.test",
                        http2=True,
                        follow_redirects=True,
                        timeout=30,
                    ) as client:
                        response = await client.post(
                            "/api/chat/messages",
                            json={"action_id": "ask", "user_input": case.user_input},
                        )
                assert response.status_code == (200 if fault == "lease" else 403)
                assert response.headers["cache-control"] == "private, no-store"
                assert "SYNTHETIC_" not in response.text
                if fault == "lease":
                    assert isinstance(
                        MessageResponse.model_validate_json(response.content).turn, NeutralTurn
                    )
                async with engine.connect() as connection:
                    assert (
                        await connection.execute(select(func.count()).select_from(TurnSource))
                    ).scalar_one() == 0
                    assert (
                        await connection.execute(
                            select(func.count())
                            .select_from(ChatTurn)
                            .where(ChatTurn.answer.is_not(None))
                        )
                    ).scalar_one() == 0
            finally:
                await engine.dispose()

    with asyncio.Runner(loop_factory=create_event_loop) as runner:
        runner.run(run())
    assert "SYNTHETIC_" not in caplog.text
