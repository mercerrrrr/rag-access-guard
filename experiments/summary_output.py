"""Private JSON and tabular exports retain denominators and provenance."""

import csv
from io import StringIO
from pathlib import Path

from pydantic import TypeAdapter

from experiments.run_storage import atomic_write, create_run_directory
from experiments.summary_types import Summary

LIMITATIONS = """Descriptive synthetic experiment, not a universal security proof.
Zero observed violations do not prove absence of untested or semantic leaks.
Only managed provenance is protected; manually supplied user text is not a DLP target.
Marker metrics are secondary literal observations; user-origin markers are separate.
Utility tests exact requested synthetic codes with document support, not general answer quality.
Fake results do not measure E5 retrieval quality or real-model answer quality.
Cache state, model identity, model-called state and observed gate order are stratified.
Logical request latency includes all attempts; attempt and stage samples are separate.
Stage release may include the experimental post-commit barrier wait.
p95 uses nearest rank; all statistics are descriptive, particularly at small n.
Repeated cases are not independent scenario types; no significance or confidence claim is made.
Warmups are counted but excluded from reported rates and timing distributions.
Only completed clean runs enter comparison; other statuses retain counts without rates.
GPU/driver and actual daemon image identity were not independently collected in earlier runs.
Previously displayed or copied text cannot be recalled after revocation.
"""


def export_summary(summary: Summary, output: Path, repository: Path) -> None:
    """Validate and serialize before reserving a new external-only output directory."""
    encoded = TypeAdapter(Summary).dump_json(summary, indent=2)
    stream = StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(
        (
            "run_id",
            "git_sha",
            "config_hash",
            "case_class",
            "cache_state",
            "model",
            "gate_order",
            "arm",
            "metric",
            "model_called",
            "numerator",
            "denominator",
            "value",
            "status",
            "n",
            "median_ms",
            "p95_ms",
            "percentile_method",
        )
    )
    for row in summary.rates:
        writer.writerow(
            (
                summary.run_id,
                summary.git_sha,
                summary.config_hash,
                row.stratum.case_class,
                row.stratum.cache_state,
                row.stratum.model,
                "|".join(row.stratum.gate_order),
                row.arm,
                row.metric,
                "",
                row.rate.numerator,
                row.rate.denominator,
                row.rate.value,
                row.rate.status,
                "",
                "",
                "",
                "",
            )
        )
    for row in summary.timings:
        writer.writerow(
            (
                summary.run_id,
                summary.git_sha,
                summary.config_hash,
                row.stratum.case_class,
                row.stratum.cache_state,
                row.stratum.model,
                "|".join(row.stratum.gate_order),
                row.arm,
                row.metric,
                row.model_called,
                "",
                "",
                "",
                "",
                row.timing.n,
                row.timing.median_ms,
                row.timing.p95_ms,
                row.timing.percentile_method,
            )
        )
    limitations = "\n".join(
        (
            f"Run: {summary.run_id}; status: {summary.status}; live: {summary.live_status}",
            f"Counts: {summary.counts.model_dump_json()}; missing: {summary.missing}",
            f"Warmups: {summary.warmups}",
            f"Distinct measured cases: {summary.measured_cases}",
            f"Repetitions: {summary.repetitions}",
            LIMITATIONS,
        )
    )
    directory = create_run_directory(output, repository)
    atomic_write(directory / "summary.json", encoded)
    atomic_write(directory / "metrics.csv", stream.getvalue().encode("utf-8"))
    atomic_write(directory / "limitations.txt", limitations.encode("utf-8"))
