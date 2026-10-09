import sys
from uuid import uuid4

import pytest
from anyio.to_thread import run_sync
from tests.unit.test_ollama_supervisor import OwnedCase, wait_file

from rag_access_guard_api.adapters.inference_runtime import (
    InferenceBusyError,
    InferenceRuntime,
    InferenceUnavailableError,
    bind_runtime,
)
from rag_access_guard_api.adapters.ollama_supervisor import OllamaSupervisor, Ownership
from rag_access_guard_api.config import Settings
from rag_access_guard_api.services.system_status import SystemReadiness

pytest_plugins = ("tests.unit.test_ollama_supervisor",)


@pytest.mark.anyio
@pytest.mark.skipif(sys.platform != "win32", reason="Owned demo uses native Windows jobs")
@pytest.mark.parametrize("owner", ["current", "foreign", "physical-quarantine"])
async def test_owned_ready_to_claim_transition_preserves_only_current_safe_busy_readiness(
    owned_case: OwnedCase,
    monkeypatch: pytest.MonkeyPatch,
    owner: str,
) -> None:
    supervisor = owned_case.supervisor
    supervisor.start()
    await run_sync(wait_file, owned_case.directory / "listening")
    config_path = owned_case.directory / "status-config.json"
    _ = config_path.write_text(supervisor.config.model_dump_json(), encoding="utf-8")
    readiness = SystemReadiness(
        Settings(
            llm_adapter="ollama",
            ollama_base_url=supervisor.config.host,
            ollama_supervisor_config_path=config_path,
        )
    )
    retained = OllamaSupervisor(supervisor.config) if owner == "foreign" else supervisor
    runtime = InferenceRuntime()
    claims: list[Ownership] = []
    observations: list[bool] = []
    real_probe = retained.has_live_inference_claim

    def transition_after_observation() -> bool:
        observed = real_probe()
        observations.append(observed)
        if not claims:
            claims.append(supervisor.begin_inference())
            if owner == "physical-quarantine":
                runtime.set_generation_available(available=False)
        return observed

    monkeypatch.setattr(retained, "has_live_inference_claim", transition_after_observation)
    try:
        _ = runtime.own_supervisor(retained)
        with bind_runtime(runtime):
            async with runtime.try_acquire_generation(uuid4()):
                result = await readiness.snapshot()
                assert result.model.state == ("ready" if owner == "current" else "unavailable")
                assert runtime.generation_busy
                if owner == "physical-quarantine":
                    assert runtime.snapshot().generation == "unavailable"
                    with pytest.raises(InferenceUnavailableError):
                        _ = runtime.try_acquire_generation(uuid4())
                else:
                    assert runtime.snapshot().generation == "busy"
                    with pytest.raises(InferenceBusyError):
                        _ = runtime.try_acquire_generation(uuid4())
                if owner == "current":
                    assert observations == [False, True]
                else:
                    assert observations
                    assert not any(observations)
                trace = (owned_case.directory / "trace.jsonl").read_text(encoding="utf-8")
                assert '"event": "/api/chat"' not in trace
    finally:
        for claim in claims:
            supervisor.complete_inference(claim)
        await runtime.aclose()


@pytest.mark.anyio
@pytest.mark.skipif(sys.platform != "win32", reason="Owned demo uses native Windows jobs")
async def test_current_owned_inference_claim_is_ready_when_busy_and_quarantine_stays_closed(
    owned_case: OwnedCase,
) -> None:
    supervisor = owned_case.supervisor
    supervisor.start()
    await run_sync(wait_file, owned_case.directory / "listening")
    config_path = owned_case.directory / "status-config.json"
    _ = config_path.write_text(supervisor.config.model_dump_json(), encoding="utf-8")
    readiness = SystemReadiness(
        Settings(
            llm_adapter="ollama",
            ollama_base_url=supervisor.config.host,
            ollama_supervisor_config_path=config_path,
        )
    )
    runtime = InferenceRuntime()
    try:
        _ = runtime.own_supervisor(supervisor)
        with bind_runtime(runtime):
            async with runtime.try_acquire_generation(uuid4()):
                claim = supervisor.begin_inference()
                assert supervisor.status() == "unavailable"
                assert (await readiness.snapshot()).model.state == "ready"
                runtime.set_generation_available(available=False)
                assert (await readiness.snapshot()).model.state == "unavailable"
                assert runtime.snapshot().generation == "unavailable"
                supervisor.complete_inference(claim)
    finally:
        await runtime.aclose()


@pytest.mark.anyio
@pytest.mark.skipif(sys.platform != "win32", reason="Owned demo uses native Windows jobs")
async def test_busy_fresh_controller_cannot_adopt_foreign_quarantined_claim(
    owned_case: OwnedCase,
) -> None:
    supervisor = owned_case.supervisor
    supervisor.start()
    await run_sync(wait_file, owned_case.directory / "listening")
    claim = supervisor.begin_inference()
    config_path = owned_case.directory / "status-config.json"
    _ = config_path.write_text(supervisor.config.model_dump_json(), encoding="utf-8")
    readiness = SystemReadiness(
        Settings(
            llm_adapter="ollama",
            ollama_base_url=supervisor.config.host,
            ollama_supervisor_config_path=config_path,
        )
    )
    runtime = InferenceRuntime()
    try:
        _ = runtime.own_supervisor(OllamaSupervisor(supervisor.config))
        with bind_runtime(runtime):
            async with runtime.try_acquire_generation(uuid4()):
                assert (await readiness.snapshot()).model.state == "unavailable"
    finally:
        supervisor.complete_inference(claim)
        await runtime.aclose()
