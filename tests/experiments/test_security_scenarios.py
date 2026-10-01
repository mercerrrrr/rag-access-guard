import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pytest
from experiments.fixture_manifest import load_fixture_manifest
from experiments.scenario_types import Scenario, ScenarioClass
from experiments.security_scenarios import SCENARIOS, load_scenarios, validate_suite

if TYPE_CHECKING:
    from pydantic import JsonValue

ROOT: Final = Path(__file__).resolve().parents[2] / "experiments"


@pytest.fixture
def suite_path(tmp_path: Path) -> Path:
    _ = shutil.copytree(ROOT / "fixtures", tmp_path / "fixtures")
    _ = shutil.copyfile(ROOT / "fixture_manifest.json", tmp_path / "fixture_manifest.json")
    _ = shutil.copyfile(ROOT / "scenarios.json", tmp_path / "scenarios.json")
    return tmp_path / "scenarios.json"


def write_case(path: Path, case: Scenario) -> None:
    _ = path.write_bytes(SCENARIOS.dump_json((case,)))


def test_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    case: dict[str, JsonValue] = {
        "id": "direct-revoke",
        "schema_version": 1,
        "class_name": "direct_revoke",
        "principal_key": "student",
        "initial_grants": [],
        "user_input": "Учебный код?",
        "actions": [],
        "expected": {
            "checks": [
                {
                    "action_id": "read",
                    "surface": "stored_read",
                    "authorization": "deny",
                    "documents": [],
                    "forbidden_documents": ["staff"],
                    "allowed_fact_keys": [],
                    "http_status": 200,
                    "body": "redacted",
                    "persisted": "unchanged",
                }
            ]
        },
    }
    path = tmp_path / "cases.json"
    _ = path.write_text(json.dumps([case, case]), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate scenario id"):
        _ = load_scenarios(path)


def test_rejects_unknown_fixture_reference(suite_path: Path) -> None:
    case = load_scenarios(suite_path)[0]
    invalid = case.model_copy(
        update={
            "initial_grants": (case.initial_grants[0].model_copy(update={"document": "missing"}),)
        }
    )
    write_case(suite_path, invalid)
    with pytest.raises(ValueError, match="unknown fixture reference"):
        _ = load_scenarios(suite_path)


def test_rejects_marker_only_oracle(suite_path: Path) -> None:
    case = load_scenarios(suite_path)[0]
    raw = case.model_dump_json()
    invalid = raw[: raw.index(',"expected":')] + ',"expected":{"marker_absent":true}}'
    _ = suite_path.write_text(f"[{invalid}]", encoding="utf-8")
    with pytest.raises(ValueError, match="checks"):
        _ = load_scenarios(suite_path)


def test_keeps_alternative_allow_control(suite_path: Path) -> None:
    cases = load_scenarios(suite_path)
    alternative = next(case for case in cases if case.class_name == ScenarioClass.ALTERNATIVE_ALLOW)
    invalid = alternative.model_copy(update={"initial_grants": alternative.initial_grants[:1]})
    write_case(suite_path, invalid)
    with pytest.raises(ValueError, match="alternative allow"):
        _ = load_scenarios(suite_path)


@pytest.mark.parametrize("case_id", ["revoke-before-release", "release-before-revoke"])
def test_race_requires_explicit_ordering(suite_path: Path, case_id: str) -> None:
    case = next(case for case in load_scenarios(suite_path) if case.id == case_id)
    mutation = case.actions[1].model_copy(update={"barrier": None})
    write_case(
        suite_path,
        case.model_copy(update={"actions": (case.actions[0], mutation, *case.actions[2:])}),
    )
    with pytest.raises(ValueError, match="explicit race ordering"):
        _ = load_scenarios(suite_path)


def test_rejects_corrupted_fixture_bytes(suite_path: Path) -> None:
    _ = (suite_path.parent / "fixtures" / "staff.txt").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="fixture hash mismatch"):
        _ = load_scenarios(suite_path)


def test_rejects_missing_coverage_class(suite_path: Path) -> None:
    cases = load_scenarios(suite_path)
    manifest = load_fixture_manifest(suite_path.with_name("fixture_manifest.json"))
    with pytest.raises(ValueError, match="missing scenario classes"):
        validate_suite(
            tuple(case for case in cases if case.class_name != ScenarioClass.ROLE_REVOKE), manifest
        )


def test_rejects_dangling_observation(suite_path: Path) -> None:
    case = load_scenarios(suite_path)[0]
    checks = tuple(
        check.model_copy(update={"action_id": "missing"}) for check in case.expected.checks
    )
    write_case(
        suite_path,
        case.model_copy(update={"expected": case.expected.model_copy(update={"checks": checks})}),
    )
    with pytest.raises(ValueError, match="unknown observation action"):
        _ = load_scenarios(suite_path)


def test_synthetic_suite_has_observable_protocol_coverage(suite_path: Path) -> None:
    cases = load_scenarios(suite_path)
    manifest = load_fixture_manifest(suite_path.with_name("fixture_manifest.json"))
    validate_suite(cases, manifest)
    assert {case.class_name for case in cases} == set(ScenarioClass)
    assert (
        next(case for case in cases if case.id == "history-after-direct-revoke").actions[-1].kind
        == "ask"
    )


@pytest.mark.parametrize(
    ("field", "value"), [("class_name", "unknown"), ("schema_version", 2), ("surprise", True)]
)
def test_rejects_unrecognized_schema_input(suite_path: Path, field: str, value: str | int) -> None:
    case = load_scenarios(suite_path)[0]
    raw: dict[str, JsonValue] = {**case.model_dump(mode="json"), field: value}
    _ = suite_path.write_text(json.dumps([raw]), encoding="utf-8")
    with pytest.raises(ValueError, match=field):
        _ = load_scenarios(suite_path)


def test_rejects_wrong_surface_for_action(suite_path: Path) -> None:
    case = load_scenarios(suite_path)[0]
    extra = case.expected.checks[0].model_copy(update={"surface": "source_read"})
    expected = case.expected.model_copy(update={"checks": (*case.expected.checks, extra)})
    write_case(suite_path, case.model_copy(update={"expected": expected}))
    with pytest.raises(ValueError, match="surface does not match action"):
        _ = load_scenarios(suite_path)


def test_rejects_protected_body_for_denied_read(suite_path: Path) -> None:
    case = next(
        case for case in load_scenarios(suite_path) if case.id == "stored-read-after-revoke"
    )
    checks = tuple(
        check.model_copy(update={"body": "protected"}) if check.surface == "stored_read" else check
        for check in case.expected.checks
    )
    write_case(
        suite_path,
        case.model_copy(update={"expected": case.expected.model_copy(update={"checks": checks})}),
    )
    with pytest.raises(ValueError, match="denied observation contains protected data"):
        _ = load_scenarios(suite_path)


def test_history_control_requires_a_second_question(suite_path: Path) -> None:
    case = next(
        case for case in load_scenarios(suite_path) if case.id == "history-after-direct-revoke"
    )
    expected = case.expected.model_copy(
        update={
            "checks": tuple(check for check in case.expected.checks if check.action_id != "repeat")
        }
    )
    write_case(
        suite_path, case.model_copy(update={"actions": case.actions[:-1], "expected": expected})
    )
    with pytest.raises(ValueError, match="history requires a subsequent ask"):
        _ = load_scenarios(suite_path)


def test_race_phase_must_match_barrier_event(suite_path: Path) -> None:
    case = next(case for case in load_scenarios(suite_path) if case.id == "revoke-before-release")
    actions = (
        case.actions[0],
        case.actions[1].model_copy(update={"phase": "after_release"}),
        *case.actions[2:],
    )
    write_case(suite_path, case.model_copy(update={"actions": actions}))
    with pytest.raises(ValueError, match="explicit race ordering"):
        _ = load_scenarios(suite_path)


@pytest.mark.parametrize("case_id", ["direct-revoke", "role-revoke", "membership-removal"])
def test_revoke_requires_an_initial_allow_edge(suite_path: Path, case_id: str) -> None:
    case = next(case for case in load_scenarios(suite_path) if case.id == case_id)
    write_case(suite_path, case.model_copy(update={"initial_grants": ()}))
    with pytest.raises(ValueError, match="mutation has no initial allow edge"):
        _ = load_scenarios(suite_path)


def test_integrity_class_requires_its_declared_fault(suite_path: Path) -> None:
    case = next(case for case in load_scenarios(suite_path) if case.id == "provenance-missing")
    fault = case.actions[0].model_copy(update={"mode": "swap", "attached_document": "public"})
    write_case(suite_path, case.model_copy(update={"actions": (fault, *case.actions[1:])}))
    with pytest.raises(ValueError, match="integrity class requires matching fault"):
        _ = load_scenarios(suite_path)


@pytest.mark.parametrize(
    ("case_id", "surface"),
    [
        ("stored-read-after-revoke", "stored_read"),
        ("source-read-after-revoke", "source_read"),
        ("document-list-after-revoke", "document_list"),
    ],
)
def test_every_read_requires_an_observation(suite_path: Path, case_id: str, surface: str) -> None:
    case = next(case for case in load_scenarios(suite_path) if case.id == case_id)
    expected = case.expected.model_copy(
        update={
            "checks": tuple(check for check in case.expected.checks if check.surface != surface)
        }
    )
    write_case(suite_path, case.model_copy(update={"expected": expected}))
    with pytest.raises(ValueError, match="action requires all surface observations"):
        _ = load_scenarios(suite_path)


@pytest.mark.parametrize(
    ("surface", "field", "value"),
    [
        ("model_context", "llm_calls", 0),
        ("model_context", "llm_calls", None),
        ("model_context", "http_status", 200),
        ("model_context", "persisted", "available"),
        ("release", "http_status", 404),
        ("release", "http_status", None),
        ("release", "persisted", "unchanged"),
        ("release", "llm_calls", 1),
    ],
)
def test_rejects_inconsistent_surface_oracle(
    suite_path: Path,
    surface: str,
    field: str,
    value: str | int | None,
) -> None:
    case = load_scenarios(suite_path)[0]
    checks = tuple(
        check.model_copy(update={field: value}) if check.surface == surface else check
        for check in case.expected.checks
    )
    expected = case.expected.model_copy(update={"checks": checks})
    write_case(suite_path, case.model_copy(update={"expected": expected}))
    with pytest.raises(ValueError, match="inconsistent surface oracle"):
        _ = load_scenarios(suite_path)
