"""Referential and observation integrity of a parsed experiment case."""

from typing import Final, assert_never

from experiments.fixture_manifest import FixtureManifest
from experiments.scenario_oracle import validate_oracle
from experiments.scenario_types import (
    Action,
    Ask,
    DirectRevoke,
    PolicyFailure,
    ReadDocuments,
    ReadSource,
    ReadThread,
    RemoveMembership,
    ReplaceVersion,
    RoleRevoke,
    Scenario,
    SessionChange,
    SurfaceExpectation,
    TamperProvenance,
)

SURFACES: Final[dict[str, frozenset[str]]] = {
    "ask": frozenset({"model_context", "release"}),
    "read_thread": frozenset({"stored_read"}),
    "read_source": frozenset({"source_read"}),
    "read_documents": frozenset({"document_list"}),
}


def _action_references(action: Action) -> tuple[str, ...]:
    match action:
        case DirectRevoke() | RoleRevoke():
            return (action.document,)
        case ReadSource():
            if action.unknown == (action.ask_id is not None):
                message = "source requires an earlier ask or unknown identity"
                raise ValueError(message)
            return (action.document,)
        case ReplaceVersion():
            return (action.document, action.replacement)
        case TamperProvenance():
            return (action.document,) + (
                (action.attached_document,) if action.attached_document is not None else ()
            )
        case (
            Ask()
            | ReadThread()
            | RemoveMembership()
            | SessionChange()
            | ReadDocuments()
            | PolicyFailure()
        ):
            return ()
        case _:
            assert_never(action)


def _validate_ordering(case: Scenario) -> set[str]:
    asks: set[str] = set()
    action_ids: set[str] = set()
    for action in case.actions:
        if action.id in action_ids:
            message = "duplicate action id"
            raise ValueError(message)
        action_ids.add(action.id)
        match action:
            case Ask():
                asks.add(action.id)
            case DirectRevoke():
                if action.barrier is not None and action.barrier.ask_id not in asks:
                    message = "barrier must reference an earlier ask"
                    raise ValueError(message)
            case ReadThread() | ReadSource():
                if action.ask_id is not None and action.ask_id not in asks:
                    message = "read must reference an earlier ask"
                    raise ValueError(message)
            case (
                RoleRevoke()
                | ReplaceVersion()
                | TamperProvenance()
                | RemoveMembership()
                | SessionChange()
                | ReadDocuments()
                | PolicyFailure()
            ):
                pass
            case _:
                assert_never(action)
    return asks


def _validate_surface(check: SurfaceExpectation, action: Action) -> None:
    if check.surface not in SURFACES.get(action.kind, frozenset()):
        message = "surface does not match action"
        raise ValueError(message)
    match check.authorization:
        case "deny" | "neutral":
            if check.documents or check.allowed_fact_keys or check.body == "protected":
                message = "denied observation contains protected data"
                raise ValueError(message)
        case "allow":
            if (
                not check.documents
                or not check.allowed_fact_keys
                or check.body not in {"protected", "list"}
            ):
                message = "allowed observation requires documents and facts"
                raise ValueError(message)
        case _:
            assert_never(check.authorization)


def _validate_observations(case: Scenario, manifest: FixtureManifest) -> set[tuple[str, str]]:
    facts = {fact for fixture in manifest.fixtures for fact in fixture.fact_keys}
    action_ids = {action.id for action in case.actions}
    actions = {action.id: action for action in case.actions}
    observed: set[tuple[str, str]] = set()
    for check in case.expected.checks:
        if check.action_id not in action_ids:
            message = "unknown observation action"
            raise ValueError(message)
        identity = (check.action_id, check.surface)
        if identity in observed:
            message = "duplicate surface observation"
            raise ValueError(message)
        _validate_surface(check, actions[check.action_id])
        validate_oracle(check)
        observed.add(identity)
        if set(check.documents) & set(check.forbidden_documents):
            message = "contradictory document oracle"
            raise ValueError(message)
        allowed_facts = {
            fact
            for fixture in manifest.fixtures
            if fixture.key in check.documents
            for fact in fixture.fact_keys
        }
        if not set(check.allowed_fact_keys) <= facts & allowed_facts:
            message = "unknown or unauthorized fact oracle"
            raise ValueError(message)
    return observed


def validate_case(case: Scenario, manifest: FixtureManifest) -> None:
    """Reject dangling fixture/action references and unobservable expectations."""
    _ = _validate_ordering(case)
    observed = _validate_observations(case, manifest)
    references = {grant.document for grant in case.initial_grants}
    references.update(ref for action in case.actions for ref in _action_references(action))
    references.update(
        ref
        for check in case.expected.checks
        for ref in (*check.documents, *check.forbidden_documents)
    )
    if not references <= {fixture.key for fixture in manifest.fixtures}:
        message = "unknown fixture reference"
        raise ValueError(message)
    if not case.actions:
        message = "scenario requires meaningful actions"
        raise ValueError(message)
    for action in case.actions:
        required = {(action.id, surface) for surface in SURFACES.get(action.kind, frozenset())}
        if not required <= observed:
            message = "action requires all surface observations"
            raise ValueError(message)
