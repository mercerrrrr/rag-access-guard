"""Compare structural outcomes without claiming identical latency or live model text."""

import argparse
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from experiments.reproduction_outcomes import normalized_arm
from experiments.run_environment import git_state
from experiments.run_manifest import RunManifest, validate_reproduction
from experiments.run_reproduction import load_reproduction
from experiments.security_scenarios import load_scenarios
from experiments.summarize import load_summary
from experiments.summary_types import Summary
from experiments.trials import TrialRecord


def assert_trial_outcomes(
    original: tuple[TrialRecord, ...], repeated: tuple[TrialRecord, ...]
) -> None:
    """Compare decisions for each scheduled trial without pooling repetitions."""
    if len(original) != len(repeated) or any(
        first.trial != second.trial
        or tuple(normalized_arm(arm.result) for arm in first.arms)
        != tuple(normalized_arm(arm.result) for arm in second.arms)
        for first, second in zip(original, repeated, strict=True)
    ):
        message = "Reproduction trial decisions differ"
        raise ValueError(message)


def assert_reproduced(original: Summary, repeated: Summary) -> None:
    """Require separate completed runs with matching controlled outcomes and sample sizes."""
    if (
        original.run_id == repeated.run_id
        or original.status != "completed"
        or repeated.status != "completed"
        or original.missing != 0
        or repeated.missing != 0
        or original.measured_cases == 0
        or replace(original, run_id="", timings=()) != replace(repeated, run_id="", timings=())
    ):
        message = "Structural reproduction differs or is incomplete"
        raise ValueError(message)


def verify_runs(original: Path, repeated: Path, repository: Path, commit_sha: str) -> Summary:
    """Validate persisted pairs and recompute outcomes against the release corpus."""
    first = load_reproduction(original / "manifest.json")
    second = load_reproduction(repeated / "manifest.json")
    validate_run_pair(first, second, commit_sha)
    scenarios = repository / "experiments/scenarios.json"
    first_summary = load_summary(original, scenarios)
    second_summary = load_summary(repeated, scenarios)
    assert_reproduced(first_summary, second_summary)
    assert_trial_outcomes(
        tuple(
            TrialRecord.model_validate_json(line)
            for line in (original / "records.jsonl").read_bytes().splitlines()
        ),
        tuple(
            TrialRecord.model_validate_json(line)
            for line in (repeated / "records.jsonl").read_bytes().splitlines()
        ),
    )
    if first_summary.measured_cases != len(load_scenarios(scenarios)):
        message = "Reproduction does not cover the complete release suite"
        raise ValueError(message)
    return first_summary


class Arguments(argparse.Namespace):
    """Typed paths parsed at the command boundary."""

    original: Path = Path()
    repeated: Path = Path()
    repository: Path = Path()


def main(arguments: list[str] | None = None) -> int:
    """Report reproduction only for clean code and independently admitted records."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("original", "repeated", "repository"):
        _ = parser.add_argument(f"--{name}", type=Path, required=True)
    options = parser.parse_args(arguments, namespace=Arguments())
    try:
        repository = options.repository.resolve(strict=True)
        state = git_state(repository)
        if state.dirty:
            _ = sys.stderr.write("Reproduction verification requires clean code\n")
            return 2
        summary = verify_runs(
            options.original.resolve(strict=True),
            options.repeated.resolve(strict=True),
            repository,
            state.sha,
        )
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        _ = sys.stderr.write("Reproduction rejected: inconsistent or missing evidence\n")
        return 2
    _ = sys.stdout.write(
        json.dumps(
            {
                "status": "pass",
                "commit_sha": summary.git_sha,
                "measured_cases": summary.measured_cases,
                "rate_rows": len(summary.rates),
                "live_status": summary.live_status,
            }
        )
        + "\n"
    )
    return 0


def validate_run_pair(original: RunManifest, repeated: RunManifest, commit_sha: str) -> None:
    """Bind the second execution to the original full, clean fake run at one commit."""
    validate_reproduction(original, repeated)
    if (
        original.identity.git_sha != commit_sha
        or repeated.status != "completed"
        or repeated.run_id == original.run_id
        or repeated.reproduces_run_id != original.run_id
        or original.identity.config.get("llm") != "fake"
    ):
        message = "Reproduction does not prove two linked fake runs at the release commit"
        raise ValueError(message)


if __name__ == "__main__":
    raise SystemExit(main())
