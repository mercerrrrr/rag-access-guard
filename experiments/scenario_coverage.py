"""Structural prerequisites for named threat and control classes."""

from collections.abc import Callable
from typing import Final, assert_never

from experiments.scenario_types import (
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
    ScenarioClass,
    SessionChange,
    TamperProvenance,
)

REQUIRED_ACTIONS: Final[dict[ScenarioClass, frozenset[str]]] = {
    ScenarioClass.ALLOW: frozenset({"ask"}),
    ScenarioClass.DENY: frozenset({"ask"}),
    ScenarioClass.NO_CONTEXT: frozenset({"ask"}),
    ScenarioClass.ADMIN_WITHOUT_GRANT: frozenset({"ask"}),
    ScenarioClass.DIRECT_REVOKE: frozenset({"ask", "revoke_direct", "read_thread"}),
    ScenarioClass.ROLE_REVOKE: frozenset({"ask", "revoke_role", "read_thread"}),
    ScenarioClass.MEMBERSHIP_REMOVAL: frozenset({"ask", "remove_membership", "read_thread"}),
    ScenarioClass.ALTERNATIVE_ALLOW: frozenset(
        {"ask", "remove_membership", "revoke_direct", "read_thread"}
    ),
    ScenarioClass.HISTORY: frozenset({"ask", "revoke_direct"}),
    ScenarioClass.VERSION_REPLACEMENT: frozenset(
        {"ask", "replace_version", "read_thread", "read_source"}
    ),
    ScenarioClass.PROVENANCE_MISSING: frozenset({"tamper_provenance", "ask"}),
    ScenarioClass.PROVENANCE_SWAP: frozenset({"tamper_provenance", "ask"}),
    ScenarioClass.POLICY_FAILURE: frozenset({"policy_failure", "ask"}),
    ScenarioClass.REVOKE_BEFORE_RELEASE: frozenset({"ask", "revoke_direct"}),
    ScenarioClass.RELEASE_BEFORE_REVOKE: frozenset({"ask", "revoke_direct"}),
    ScenarioClass.STORED_READ: frozenset({"ask", "revoke_direct", "read_thread"}),
    ScenarioClass.SOURCE_READ: frozenset({"ask", "revoke_direct", "read_source"}),
    ScenarioClass.DOCUMENT_LIST: frozenset({"ask", "revoke_direct", "read_documents"}),
    ScenarioClass.UNKNOWN_SOURCE: frozenset({"read_source"}),
    ScenarioClass.LOGOUT: frozenset({"ask", "logout", "read_thread"}),
    ScenarioClass.SESSION_EXPIRY: frozenset({"ask", "expire_session", "read_thread"}),
    ScenarioClass.INACTIVE_USER: frozenset({"ask", "deactivate_principal", "read_thread"}),
    ScenarioClass.MANUAL_PASTE: frozenset({"ask"}),
    ScenarioClass.PROMPT_INJECTION: frozenset({"ask"}),
    ScenarioClass.UNTRUSTED_MARKUP: frozenset({"ask"}),
    ScenarioClass.FALSE_ATTACHMENT: frozenset({"tamper_provenance", "ask"}),
    ScenarioClass.OWNERSHIP: frozenset({"ask", "read_thread"}),
}


def _validate_union(case: Scenario) -> None:
    direct = tuple(grant for grant in case.initial_grants if grant.kind == "direct")
    roles = tuple(grant for grant in case.initial_grants if grant.kind == "role")
    if not any(
        grant.user == case.principal_key
        and grant.document == role.document
        and case.principal_key in role.members
        for grant in direct
        for role in roles
    ) or not {"allow", "deny"} <= {
        check.authorization for check in case.expected.checks if check.surface == "stored_read"
    }:
        message = "alternative allow requires independent grants and both read outcomes"
        raise ValueError(message)


def _validate_race(case: Scenario) -> None:
    race_order = {
        ScenarioClass.REVOKE_BEFORE_RELEASE: ("model_generated", "generation"),
        ScenarioClass.RELEASE_BEFORE_REVOKE: ("release_committed", "after_release"),
    }
    expected_event, expected_phase = race_order[case.class_name]
    revokes = tuple(action for action in case.actions if action.kind == "revoke_direct")
    if (
        len(revokes) != 1
        or revokes[0].barrier is None
        or revokes[0].barrier.event != expected_event
        or revokes[0].phase != expected_phase
    ):
        message = "explicit race ordering is required"
        raise ValueError(message)


def _validate_history(case: Scenario) -> None:
    kinds = [action.kind for action in case.actions]
    revoke_index = kinds.index("revoke_direct")
    if "ask" not in kinds[:revoke_index] or "ask" not in kinds[revoke_index + 1 :]:
        message = "history requires a subsequent ask"
        raise ValueError(message)


def _validate_grants(case: Scenario) -> None:
    direct = {
        (grant.user, grant.document) for grant in case.initial_grants if grant.kind == "direct"
    }
    roles = {(grant.role, grant.document) for grant in case.initial_grants if grant.kind == "role"}
    members = {
        (grant.role, user)
        for grant in case.initial_grants
        if grant.kind == "role"
        for user in grant.members
    }
    for action in case.actions:
        match action:
            case DirectRevoke():
                present = (action.user, action.document) in direct
            case RoleRevoke():
                present = (action.role, action.document) in roles
            case RemoveMembership():
                present = (action.role, action.user) in members
            case (
                Ask()
                | ReplaceVersion()
                | SessionChange()
                | ReadThread()
                | ReadSource()
                | ReadDocuments()
                | PolicyFailure()
                | TamperProvenance()
            ):
                continue
            case _:
                assert_never(action)
        if not present:
            message = "mutation has no initial allow edge"
            raise ValueError(message)


def _validate_integrity(case: Scenario) -> None:
    modes = {
        ScenarioClass.PROVENANCE_MISSING: "missing",
        ScenarioClass.PROVENANCE_SWAP: "swap",
        ScenarioClass.FALSE_ATTACHMENT: "false_attachment",
    }
    faults = tuple(action for action in case.actions if action.kind == "tamper_provenance")
    expected_mode = modes[case.class_name]
    if len(faults) != 1 or faults[0].mode != expected_mode:
        message = "integrity class requires matching fault"
        raise ValueError(message)
    if (faults[0].attached_document is not None) != (expected_mode in {"swap", "false_attachment"}):
        message = "integrity fault requires an explicit attachment witness"
        raise ValueError(message)


CLASS_CHECKS: Final[dict[ScenarioClass, Callable[[Scenario], None]]] = {
    ScenarioClass.ALTERNATIVE_ALLOW: _validate_union,
    ScenarioClass.REVOKE_BEFORE_RELEASE: _validate_race,
    ScenarioClass.RELEASE_BEFORE_REVOKE: _validate_race,
    ScenarioClass.HISTORY: _validate_history,
    ScenarioClass.PROVENANCE_MISSING: _validate_integrity,
    ScenarioClass.PROVENANCE_SWAP: _validate_integrity,
    ScenarioClass.FALSE_ATTACHMENT: _validate_integrity,
}


def validate_class(case: Scenario) -> None:
    """Require executable actions, union controls and explicit race ordering."""
    if not REQUIRED_ACTIONS[case.class_name] <= {action.kind for action in case.actions}:
        message = "scenario class lacks required actions"
        raise ValueError(message)
    extra_check = CLASS_CHECKS.get(case.class_name)
    if extra_check is not None:
        extra_check(case)
    _validate_grants(case)
