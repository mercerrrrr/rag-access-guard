"""Build the pinned text-only demo artifact in a stopped, privately owned Ollama store."""

import sys
from pathlib import Path

import anyio
import typer

from rag_access_guard_api.adapters.ollama_http import create_client
from rag_access_guard_api.adapters.ollama_supervisor import OllamaSupervisor, load_supervisor_config
from rag_access_guard_api.adapters.text_model_provisioning import provision_text_model
from rag_access_guard_api.schemas.generation import GenerationUnavailable
from rag_access_guard_api.server import create_event_loop


async def provision(config_path: Path) -> None:
    """Never mutate a shared/default Ollama instance or an active demo process."""
    supervisor = OllamaSupervisor(load_supervisor_config(config_path))
    if supervisor.status() != "stopped":
        raise GenerationUnavailable
    try:
        supervisor.start(require_stopped=True)
        with anyio.fail_after(15):
            while supervisor.status() == "starting":  # noqa: ASYNC110 -- external process readiness.
                await anyio.sleep(0.1)
        with supervisor.hold_containment():
            async with create_client(supervisor.config.host) as client:
                await provision_text_model(client)
    finally:
        supervisor.close_recovery_child()


def main(config_path: Path) -> None:
    """Accept only an explicit absolute private supervisor configuration."""
    anyio.run(provision, config_path, backend_options={"loop_factory": create_event_loop})
    _ = sys.stdout.write("Text-only model verified; owned engine stopped.\n")


if __name__ == "__main__":
    typer.run(main)
