from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest
from experiments.metrics import make_rate
from experiments.summary_aggregation import summarize_run
from experiments.summary_types import Summary
from experiments.verify_reproduction import assert_reproduced, validate_run_pair
from tests.experiments.summary_fixtures import paired_record, persist_run
from tests.experiments.test_summary_aggregation import summary_input


def summary(tmp_path: Path) -> Summary:
    manifest, record, case = summary_input(tmp_path)
    return summarize_run(manifest, (record,), (case,), {"public.code": "SYNTHETIC_PUBLIC_61"})


@pytest.mark.parametrize(
    "damage", ["same_run", "sha", "config", "status", "missing", "rates", "cases", "repetitions"]
)
def test_reproduction_refuses_changed_outcomes(tmp_path: Path, damage: str) -> None:
    original = summary(tmp_path)
    repeated = replace(original, run_id="00000000-0000-0000-0000-000000000002")
    match damage:
        case "same_run":
            repeated = original
        case "sha":
            repeated = replace(repeated, git_sha="b" * 40)
        case "config":
            repeated = replace(repeated, config_hash="b" * 64)
        case "status":
            repeated = replace(repeated, status="failed")
        case "missing":
            repeated = replace(repeated, missing=1)
        case "rates":
            changed = replace(original.rates[0], rate=make_rate(0, 1))
            repeated = replace(repeated, rates=(changed, *original.rates[1:]))
        case "cases":
            repeated = replace(repeated, measured_cases=0)
        case "repetitions":
            repeated = replace(repeated, repetitions=())
        case _:
            pytest.fail("Unknown damage")
    with pytest.raises(ValueError, match="reprodu"):
        assert_reproduced(original, repeated)


def test_reproduction_does_not_require_identical_timings(tmp_path: Path) -> None:
    original = summary(tmp_path)
    repeated = replace(original, run_id="00000000-0000-0000-0000-000000000002", timings=())
    assert_reproduced(original, repeated)


@pytest.mark.parametrize("damage", ["sha", "link", "same_run", "status", "corpus", "live"])
def test_reproduction_refuses_changed_manifest(tmp_path: Path, damage: str) -> None:
    original = persist_run(tmp_path, paired_record())
    repeated = original.model_copy(
        update={"run_id": UUID(int=2), "reproduces_run_id": original.run_id}
    )
    match damage:
        case "sha":
            original = original.model_copy(
                update={"identity": original.identity.model_copy(update={"git_sha": "f" * 40})}
            )
        case "link":
            repeated = repeated.model_copy(update={"reproduces_run_id": None})
        case "same_run":
            repeated = repeated.model_copy(update={"run_id": original.run_id})
        case "status":
            repeated = repeated.model_copy(update={"status": "failed"})
        case "corpus":
            repeated = repeated.model_copy(
                update={"identity": repeated.identity.model_copy(update={"corpus_hash": "f" * 64})}
            )
        case "live":
            original = original.model_copy(
                update={
                    "identity": original.identity.model_copy(
                        update={"config": {**original.identity.config, "llm": "ollama"}}
                    )
                }
            )
            repeated = repeated.model_copy(update={"identity": original.identity})
        case _:
            pytest.fail("Unknown damage")
    with pytest.raises(ValueError, match=r"[Rr]eprodu"):
        validate_run_pair(original, repeated, "d" * 40)


def test_reproduction_accepts_linked_clean_fake_manifests(tmp_path: Path) -> None:
    original = persist_run(tmp_path, paired_record())
    repeated = original.model_copy(
        update={"run_id": UUID(int=2), "reproduces_run_id": original.run_id}
    )
    validate_run_pair(original, repeated, "d" * 40)
