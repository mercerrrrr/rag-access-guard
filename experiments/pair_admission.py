"""Semantic binding of duplicated pair evidence to its admitted trial."""

from experiments.comparison import assert_comparable
from experiments.observations import PairResult
from experiments.run_config import RunConfig
from experiments.run_manifest import RunIdentity
from experiments.trials import TrialRecord


def admit_pair(record: TrialRecord, identity: RunIdentity) -> PairResult:
    """A complete label cannot override contradictory nested results or identities."""
    pair = record.pair
    if (
        record.status != "completed"
        or record.error_code is not None
        or pair is None
        or (pair.case_id, pair.seed, pair.order)
        != (record.trial.case_id, record.trial.seed, record.trial.order)
        or len(record.arms) != len(record.trial.order)
        or set(record.trial.order) != {"baseline", "guarded"}
        or tuple(arm.result.arm for arm in record.arms) != record.trial.order
    ):
        message = "Incomplete or inconsistent pair metadata"
        raise ValueError(message)
    results = {"baseline": pair.baseline, "guarded": pair.guarded}
    if any(
        arm.status != "completed"
        or arm.result != results[arm.result.arm]
        or arm.result.config_hash != identity.trial_config_hashes.get(record.trial.trial_id)
        or arm.result.corpus_hash != identity.corpus_hash
        for arm in record.arms
    ):
        message = "Inconsistent pair results or admitted inputs"
        raise ValueError(message)
    assert_comparable(pair)
    settings = identity.config.get("settings")
    if settings is None:
        message = "Missing admitted adapter settings"
        raise ValueError(message)
    config = RunConfig.model_validate(settings).comparison
    expected = (config.model_identity, config.tokenizer_identity, config.renderer_identity)
    if any(
        (arm.model_identity, arm.tokenizer_identity, arm.renderer_identity) != expected
        for arm in (pair.baseline, pair.guarded)
    ):
        message = "Recorded adapter identity differs from admitted settings"
        raise ValueError(message)
    mode = identity.config.get("llm")
    model = config.model_manifest
    expected_mode = "fake" if model is None else "ollama"
    expected_tag = config.model_identity if model is None else model.model_name
    if mode != expected_mode or identity.model_tag != expected_tag:
        message = "Recorded adapter mode differs from admitted settings"
        raise ValueError(message)
    return pair
