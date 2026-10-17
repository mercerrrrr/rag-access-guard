import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from tests.unit.test_ollama_supervisor import OwnedCase, owned_case, wait_file

from rag_access_guard_api.adapters.ollama_supervisor import (
    OllamaSupervisor,
    SupervisorConfig,
)
from rag_access_guard_api.cli import provision_text_model as cli
from rag_access_guard_api.schemas.generation import GenerationUnavailable, InferenceUnavailableError

if TYPE_CHECKING:
    import httpx2

__all__ = ["owned_case"]


@pytest.mark.anyio
@pytest.mark.skipif(sys.platform != "win32", reason="Owned demo uses native Windows jobs")
@pytest.mark.parametrize("failure", ["competing_wins", "failed_start", "own_provision_failure"])
async def test_provisioning_cleanup_never_reaps_a_competing_owner(
    owned_case: OwnedCase, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    competitor = owned_case.supervisor
    command = (
        sys.executable,
        str(Path(__file__).parents[1] / "helpers/controlled_ollama.py"),
        str(owned_case.directory),
    )
    controller = OllamaSupervisor(competitor.config, command=command)
    original_start = controller.start
    mutations: list[bool] = []

    def start(*, require_stopped: bool = False) -> None:
        if failure != "own_provision_failure":
            competitor.start()
            wait_file(owned_case.directory / "listening")
        if failure == "failed_start":
            raise GenerationUnavailable
        if require_stopped:
            original_start(require_stopped=True)
        else:
            original_start()

    async def mutate(_: "httpx2.AsyncClient") -> None:
        mutations.append(True)
        if failure == "own_provision_failure":
            raise GenerationUnavailable

    def controller_for(_: SupervisorConfig) -> OllamaSupervisor:
        return controller

    def config_for(_: Path) -> SupervisorConfig:
        return competitor.config

    monkeypatch.setattr(controller, "start", start)
    monkeypatch.setattr(cli, "OllamaSupervisor", controller_for)
    monkeypatch.setattr(cli, "load_supervisor_config", config_for)
    monkeypatch.setattr(cli, "provision_text_model", mutate)

    try:
        expected_error = (
            InferenceUnavailableError if failure == "competing_wins" else GenerationUnavailable
        )
        with pytest.raises(expected_error):
            await cli.provision(owned_case.directory / "config.json")

        if failure == "own_provision_failure":
            assert mutations == [True]
            assert controller.status() == "stopped"
        else:
            assert not mutations
            assert competitor.status() == "ready"
            assert all(member.alive() for member in competitor.owned_processes())
    finally:
        controller.close_recovery_child()
        competitor.close_recovery_child()
