import csv
from pathlib import Path

import pytest
from experiments.summary_aggregation import summarize_run
from experiments.summary_output import export_summary
from experiments.summary_types import Summary
from pydantic import TypeAdapter
from tests.experiments.test_summary_aggregation import summary_input

from experiments import summarize


def test_bad_manifest_does_not_create_output(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    _ = (run / "manifest.json").write_text("{}", encoding="utf-8")
    output = tmp_path / "summary"
    assert summarize.main(["--run", str(run), "--output", str(output)]) == 2
    assert not output.exists()


def test_changed_records_do_not_create_output(tmp_path: Path) -> None:
    _ = summary_input(tmp_path)
    with (tmp_path / "records.jsonl").open("a", encoding="utf-8") as stream:
        _ = stream.write("{}\n")
    output = tmp_path / "summary"
    assert summarize.main(["--run", str(tmp_path), "--output", str(output)]) == 2
    assert not output.exists()


def test_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        _ = summarize.main(["--help"])
    assert error.value.code == 0
    assert "--scenarios" in capsys.readouterr().out


def test_export_preserves_denominators_and_rejects_overwrite(tmp_path: Path) -> None:
    manifest, record, case = summary_input(tmp_path)
    summary = summarize_run(manifest, (record,), (case,), {"public.code": "SYNTHETIC_PUBLIC_61"})
    output = tmp_path / "summary"
    repository = tmp_path / "repository"
    export_summary(summary, output, repository)
    data = TypeAdapter(Summary).validate_json((output / "summary.json").read_bytes())
    assert data.counts.completed == 1
    with (output / "metrics.csv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    utility = next(row for row in rows if row["metric"] == "allowed_utility")
    assert (utility["numerator"], utility["denominator"], utility["git_sha"]) == (
        "1",
        "1",
        "d" * 40,
    )
    with pytest.raises(FileExistsError):
        export_summary(summary, output, repository)
    with pytest.raises(ValueError, match="outside"):
        export_summary(summary, repository / "summary", repository)
