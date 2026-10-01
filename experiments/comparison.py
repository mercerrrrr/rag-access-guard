"""Pair orchestration isolated from every production entry point."""

from hashlib import sha256
from random import Random
from typing import TYPE_CHECKING

from experiments.comparison_config import ComparisonConfig
from experiments.observations import Arm, ArmRecord, PairResult
from experiments.scenario_types import Scenario

if TYPE_CHECKING:
    from experiments.harness import ScenarioHarness

__all__ = ["ComparisonConfig", "assert_comparable", "run_pair"]


async def run_pair(
    case: Scenario,
    config: ComparisonConfig,
    harness: "ScenarioHarness",
    *,
    records: list[ArmRecord] | None = None,
) -> PairResult:
    """Execute both arms from separate clean databases in seeded order."""
    config.validate_runtime()
    order: tuple[Arm, Arm] = (
        ("baseline", "guarded")
        if Random(config.seed).getrandbits(1)  # noqa: S311 -- experimental order, not credentials.
        else ("guarded", "baseline")
    )
    async with harness.open_pair(case, config) as snapshot:
        try:
            results = {arm: await snapshot.run_arm(case, config, arm) for arm in order}
        finally:
            if records is not None:
                records.extend(snapshot.arm_records)
    pair = PairResult(
        case_id=case.id,
        seed=config.seed,
        order=order,
        baseline=results["baseline"],
        guarded=results["guarded"],
        comparison_fingerprint=sha256(
            (
                case.model_dump_json()
                + results["baseline"].config_hash
                + results["baseline"].corpus_hash
            ).encode()
        ).hexdigest(),
    )
    assert_comparable(pair)
    return pair


def assert_comparable(pair: PairResult) -> None:
    """Reject treatment-confounding input identities, not expected context differences."""
    left, right = pair.baseline, pair.guarded
    if (
        left.arm != "baseline"
        or right.arm != "guarded"
        or left.config_hash != right.config_hash
        or left.corpus_hash != right.corpus_hash
        or left.renderer_identity != right.renderer_identity
        or left.tokenizer_identity != right.tokenizer_identity
        or left.model_identity != right.model_identity
        or left.documents != right.documents
    ):
        message = "Incomparable experiment arms"
        raise ValueError(message)
