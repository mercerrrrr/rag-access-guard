from dataclasses import replace
from pathlib import Path

import pytest
from experiments.run_reproduction import load_reproduction
from tests.experiments.summary_fixtures import paired_record, persist_run


@pytest.mark.parametrize("damage", ["case", "seed", "order", "arm", "config"])
def test_reproduction_rejects_semantically_contradictory_pair(tmp_path: Path, damage: str) -> None:
    record = paired_record()
    assert record.pair is not None
    pair = record.pair
    match damage:
        case "case":
            pair = replace(pair, case_id="other")
        case "seed":
            pair = replace(pair, seed=8)
        case "order":
            pair = replace(pair, order=("guarded", "baseline"))
        case "arm":
            pair = replace(pair, guarded=replace(pair.guarded, model_identity="other"))
        case "config":
            pair = replace(pair, baseline=replace(pair.baseline, config_hash="f" * 64))
        case _:
            pytest.fail("Unknown test damage")
    _ = persist_run(tmp_path, record.model_copy(update={"pair": pair}))
    with pytest.raises(ValueError, match="pair"):
        _ = load_reproduction(tmp_path / "manifest.json")


def test_consistent_pair_is_accepted(tmp_path: Path) -> None:
    expected = persist_run(tmp_path, paired_record())
    assert load_reproduction(tmp_path / "manifest.json") == expected
