"""Load the versioned experiment suite from JSON."""

from pathlib import Path
from typing import Final

from pydantic import TypeAdapter

from experiments.fixture_manifest import FixtureManifest, load_fixture_manifest
from experiments.scenario_coverage import validate_class
from experiments.scenario_types import Scenario, ScenarioClass
from experiments.scenario_validation import validate_case

SCENARIOS: Final = TypeAdapter(tuple[Scenario, ...])


def load_scenarios(path: Path) -> tuple[Scenario, ...]:
    """Parse a synthetic suite without accessing a database or network."""
    cases = SCENARIOS.validate_json(path.read_bytes())
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        message = "duplicate scenario id"
        raise ValueError(message)
    manifest = load_fixture_manifest(path.with_name("fixture_manifest.json"))
    for case in cases:
        validate_case(case, manifest)
        validate_class(case)
    return cases


def validate_suite(cases: tuple[Scenario, ...], fixture_manifest: FixtureManifest) -> None:
    """Require a complete structurally observable scenario suite."""
    missing = set(ScenarioClass) - {case.class_name for case in cases}
    if missing:
        message = f"missing scenario classes: {', '.join(sorted(missing))}"
        raise ValueError(message)
    for case in cases:
        validate_case(case, fixture_manifest)
        validate_class(case)
