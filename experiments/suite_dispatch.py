"""Closed, typed suite selection before any runtime or output access."""

import argparse
from pathlib import Path
from typing import Literal, assert_never

from pydantic import ValidationError

from experiments.demo_quality import run_demo_quality
from experiments.run_config import RunOptions
from experiments.scenario_types import FrozenModel
from experiments.security_suite import run_security
from experiments.suite_invocation import DemoQualityInvocation, SecurityInvocation, SuiteInvocation

__all__ = [
    "DemoQualityInvocation",
    "SecurityInvocation",
    "SuiteInvocation",
    "parse_invocation",
    "run_suite",
]


class ParsedOptions(FrozenModel):
    """Parse argparse's dynamic namespace once at its untrusted boundary."""

    suite: Literal["security", "demo-quality"]
    scenarios: Path | None
    config: Path | None
    seed: int | None
    repetitions: int | None
    llm: Literal["fake", "ollama"] | None
    output: Path
    reproduce: Path | None
    mode: Literal["validate", "calibrate", "holdout", "rehearsal"] | None
    resume: bool


def parse_invocation(arguments: list[str] | None = None) -> SuiteInvocation:
    """Parse both suites while retaining every legacy security default."""
    parser = argparse.ArgumentParser(description="Run reproducible evaluation suites")
    _ = parser.add_argument("--suite", choices=("security", "demo-quality"), default="security")
    _ = parser.add_argument("--scenarios", type=Path)
    _ = parser.add_argument("--config", type=Path)
    _ = parser.add_argument("--seed", type=int)
    _ = parser.add_argument("--repetitions", type=int)
    _ = parser.add_argument("--llm", choices=("fake", "ollama"))
    _ = parser.add_argument("--output", type=Path, required=True)
    _ = parser.add_argument("--reproduce", type=Path)
    _ = parser.add_argument("--mode", choices=("validate", "calibrate", "holdout", "rehearsal"))
    _ = parser.add_argument("--resume", action="store_true")
    values = ParsedOptions.model_validate(vars(parser.parse_args(arguments)))
    try:
        match values.suite:
            case "security":
                if values.mode is not None or values.resume:
                    parser.error("security does not accept --mode or --resume")
                return SecurityInvocation(
                    options=RunOptions.model_validate(
                        {
                            "scenarios": values.scenarios or Path("experiments/scenarios.json"),
                            "config": values.config or Path("experiments/default_config.json"),
                            "seed": 7 if values.seed is None else values.seed,
                            "repetitions": 1 if values.repetitions is None else values.repetitions,
                            "llm": values.llm or "fake",
                            "output": values.output,
                            "reproduce": values.reproduce,
                        }
                    )
                )
            case "demo-quality":
                if values.mode is None or values.config is None or values.scenarios is None:
                    parser.error("demo-quality requires --mode, --config and --scenarios")
                if any(
                    value is not None
                    for value in (values.seed, values.repetitions, values.llm, values.reproduce)
                ):
                    parser.error("security controls are unavailable for demo-quality")
                return DemoQualityInvocation.model_validate(
                    {
                        "config": values.config,
                        "scenarios": values.scenarios,
                        "output": values.output,
                        "mode": values.mode,
                        "resume": values.resume,
                    }
                )
            case _:
                assert_never(values.suite)
    except ValidationError:
        parser.error("Invalid run options; check paths and positive repetitions")


def run_suite(invocation: SuiteInvocation) -> int:
    """The closed suite boundary is shared by the primary CLI and thin wrapper."""
    match invocation:
        case SecurityInvocation(options=options):
            return run_security(options)
        case DemoQualityInvocation():
            return run_demo_quality(invocation)
        case _:
            assert_never(invocation)
