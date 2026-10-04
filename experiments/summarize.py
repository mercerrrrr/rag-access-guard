"""Create private descriptive summaries from persisted paired evidence."""

import argparse
import re
import sys
from hashlib import sha256
from pathlib import Path

from experiments.fixture_manifest import load_fixture_manifest, read_fixture
from experiments.run_manifest import RunManifest
from experiments.scenario_types import FrozenModel
from experiments.security_scenarios import load_scenarios
from experiments.summary_aggregation import summarize_run
from experiments.summary_output import export_summary
from experiments.summary_types import Summary
from experiments.trials import TrialRecord


class SummaryOptions(FrozenModel):
    """Typed CLI paths are validated before the output directory is reserved."""

    run: Path
    output: Path
    scenarios: Path


def load_summary(run: Path, scenarios: Path) -> Summary:
    """Bind stored records, source scenario and fixture bytes before aggregation."""
    if not run.is_absolute():
        message = "Run path must be absolute"
        raise ValueError(message)
    manifest = RunManifest.model_validate_json((run / "manifest.json").read_bytes())
    record_path = run / "records.jsonl"
    raw = record_path.read_bytes() if record_path.exists() else b""
    if (
        manifest.records_sha256 is not None and sha256(raw).hexdigest() != manifest.records_sha256
    ) or (manifest.status == "completed" and manifest.records_sha256 is None):
        message = "Record checksum mismatch"
        raise ValueError(message)
    records = tuple(TrialRecord.model_validate_json(line) for line in raw.splitlines())
    if sha256(scenarios.read_bytes()).hexdigest() != manifest.identity.scenario_hash:
        message = "Scenario hash mismatch"
        raise ValueError(message)
    cases = load_scenarios(scenarios)
    corpus = load_fixture_manifest(scenarios.with_name("fixture_manifest.json"))
    if sha256(corpus.model_dump_json().encode()).hexdigest() != manifest.identity.corpus_hash:
        message = "Corpus hash mismatch"
        raise ValueError(message)
    rubric: dict[str, str] = {}
    for fixture in corpus.fixtures:
        markers = set(
            re.findall(r"SYNTHETIC_[A-Z0-9_]+", read_fixture(scenarios.parent, fixture).decode())
        )
        if len(markers) != 1:
            message = "Ambiguous synthetic code rubric"
            raise ValueError(message)
        rubric[f"{fixture.key}.code"] = next(iter(markers))
    return summarize_run(manifest, records, cases, rubric)


def main(arguments: list[str] | None = None) -> int:
    """Reject malformed evidence before writing any success artifact."""
    parser = argparse.ArgumentParser(description="Summarize private paired security experiments")
    _ = parser.add_argument("--run", type=Path, required=True)
    _ = parser.add_argument("--output", type=Path, required=True)
    _ = parser.add_argument("--scenarios", type=Path, default=Path("experiments/scenarios.json"))
    try:
        options = SummaryOptions.model_validate(vars(parser.parse_args(arguments)))
        summary = load_summary(options.run, options.scenarios)
        export_summary(summary, options.output, Path(__file__).resolve().parents[1])
    except (OSError, ValueError, KeyError):
        _ = sys.stderr.write("Summary rejected: inconsistent evidence, oracle or output path\n")
        return 2
    _ = sys.stdout.write(f"Summary {summary.run_id}: {summary.status}\n")
    return 0 if summary.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
