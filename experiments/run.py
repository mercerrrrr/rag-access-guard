"""Private command-line entry point for reproducible paired experiments."""

import argparse
import asyncio
import os
import sys
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.engine import make_url

from experiments.database import ExperimentDatabases
from experiments.harness import ScenarioHarness
from experiments.run_config import RunOptions, synthetic_retrieval
from experiments.run_live import verify_live
from experiments.run_manifest import validate_reproduction
from experiments.run_reproduction import load_reproduction
from experiments.run_setup import RunInputs
from experiments.run_storage import atomic_write, create_run_directory
from experiments.runner import RunExecution
from rag_access_guard_api.config import Settings
from rag_access_guard_api.schemas.generation import GenerationUnavailable
from rag_access_guard_api.server import create_event_loop


def parse_options() -> RunOptions:
    """Parse controls before creating any output or database."""
    parser = argparse.ArgumentParser(description="Run isolated paired security experiments")
    _ = parser.add_argument("--scenarios", type=Path, default=Path("experiments/scenarios.json"))
    _ = parser.add_argument("--config", type=Path, default=Path("experiments/default_config.json"))
    _ = parser.add_argument("--seed", type=int, default=7)
    _ = parser.add_argument("--repetitions", type=int, default=1)
    _ = parser.add_argument("--llm", choices=("fake", "ollama"), default="fake")
    _ = parser.add_argument("--output", type=Path, required=True)
    _ = parser.add_argument("--reproduce", type=Path)
    try:
        return RunOptions.model_validate(vars(parser.parse_args()))
    except ValidationError:
        parser.error("Invalid run options; repetitions must be positive")


def main() -> int:
    """Run the CLI without leaking raw connection exceptions to console output."""
    options = parse_options()
    repository = Path(__file__).resolve().parents[1]
    previous = os.environ.get("RAG_ACCESS_GUARD_RETRIEVAL_CONFIG_PATH")
    try:
        inputs = RunInputs.load(options, repository)
        output = create_run_directory(options.output, repository)
        retrieval = output / "retrieval.json"
        atomic_write(retrieval, synthetic_retrieval(inputs.corpus_hash).model_dump_json().encode())
        inputs = replace(
            inputs, hashes=(*inputs.hashes, (retrieval, sha256(retrieval.read_bytes()).hexdigest()))
        )
        os.environ["RAG_ACCESS_GUARD_RETRIEVAL_CONFIG_PATH"] = str(retrieval)
        manifest = inputs.manifest()
        if options.reproduce is not None:
            original = load_reproduction(options.reproduce)
            validate_reproduction(original, manifest)
            manifest = manifest.model_copy(
                update={
                    "reproduces_run_id": original.run_id,
                    "machine_differences": tuple(
                        sorted(
                            key
                            for key in original.environment.keys() | manifest.environment.keys()
                            if original.environment.get(key) != manifest.environment.get(key)
                        )
                    ),
                }
            )
        if options.llm == "ollama":
            try:
                with asyncio.Runner(loop_factory=create_event_loop) as runner:
                    runner.run(verify_live(inputs.config.comparison))
            except GenerationUnavailable:
                manifest = manifest.model_copy(
                    update={
                        "status": "not_run",
                        "live_status": "not_run",
                        "reason": "live_adapter_not_verified",
                        "live_reason": "Live adapter identity has not been verified",
                        "finished_at": datetime.now(UTC),
                    }
                )
                atomic_write(output / "manifest.json", manifest.model_dump_json(indent=2).encode())
                return 1
        url = make_url(str(Settings().database_url)).set(database="postgres")
        harness = ScenarioHarness(
            ExperimentDatabases(url.render_as_string(hide_password=False)),
            options.scenarios.with_name("fixture_manifest.json"),
        )
        execution = RunExecution(
            manifest,
            inputs.config.comparison,
            {case.id: case for case in inputs.cases},
            harness,
            output,
            verify_inputs=inputs.unchanged,
        )
        with asyncio.Runner(loop_factory=create_event_loop) as runner:
            result = runner.run(execution.run())
        _ = sys.stdout.write(f"Run {result.run_id}: {result.status}\n")
    except (OSError, ValueError, RuntimeError):
        _ = sys.stderr.write(
            "Run rejected: invalid inputs, unavailable runtime, or output conflict\n"
        )
        return 2
    else:
        return 0 if result.status == "completed" else 1
    finally:
        if previous is None:
            _ = os.environ.pop("RAG_ACCESS_GUARD_RETRIEVAL_CONFIG_PATH", None)
        else:
            os.environ["RAG_ACCESS_GUARD_RETRIEVAL_CONFIG_PATH"] = previous


if __name__ == "__main__":
    raise SystemExit(main())
