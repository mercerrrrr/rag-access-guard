#!/usr/bin/env -S uv run --frozen python
"""Operate only the private demonstration-owned Ollama instance."""

import argparse
from pathlib import Path
from threading import Event
from time import monotonic

import psutil
from pydantic import ValidationError

from rag_access_guard_api.adapters.inference_runtime import InferenceUnavailableError
from rag_access_guard_api.adapters.ollama_supervisor import OllamaSupervisor, load_supervisor_config


class Arguments(argparse.Namespace):
    """Typed operator-only command arguments."""

    action: str = "status"
    config: Path = Path()


def main() -> None:
    """Report closed runtime state, never paths, PIDs or model/request content."""
    parser = argparse.ArgumentParser()
    _ = parser.add_argument("action", choices=("start", "status", "stop"))
    _ = parser.add_argument("--config", type=Path, required=True)
    arguments = parser.parse_args(namespace=Arguments())
    try:
        config = load_supervisor_config(arguments.config)
        supervisor = OllamaSupervisor(config)
        match arguments.action:
            case "start":
                supervisor.start()
                deadline = monotonic() + 15
                interval = Event()
                while supervisor.status() == "starting" and monotonic() < deadline:
                    _ = interval.wait(0.05)
                result = supervisor.status()
            case "stop":
                supervisor.stop()
                result = supervisor.status()
            case "status":
                result = supervisor.status()
            case _:
                result = "unavailable"
    except (OSError, psutil.Error, ValidationError, InferenceUnavailableError):
        result = "unavailable"
    print(result)  # noqa: T201 -- closed operator CLI result.
    if result == "unavailable" or (arguments.action == "start" and result != "ready"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
