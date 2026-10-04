"""Descriptive rates with explicit observation denominators."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from experiments.observations import ArmResult, Observation
from experiments.scenario_types import Scenario, SurfaceExpectation
from experiments.surface_judgement import judge_surface


@dataclass(frozen=True, slots=True)
class Rate:
    """A measured fraction or explicit absence of eligible observations."""

    numerator: int
    denominator: int
    value: float | None
    status: Literal["measured", "not_applicable"]


def make_rate(numerator: int, denominator: int) -> Rate:
    """Reject impossible counts and keep an empty denominator distinct from zero."""
    if numerator < 0 or denominator < numerator:
        message = "Rate counts must satisfy 0 <= numerator <= denominator"
        raise ValueError(message)
    return Rate(
        numerator,
        denominator,
        numerator / denominator if denominator else None,
        "measured" if denominator else "not_applicable",
    )


@dataclass(frozen=True, slots=True)
class ArmMetrics:
    """Counts remain inspectable before pooling trials into descriptive strata."""

    rates: tuple[tuple[str, Rate], ...]
    forbidden_ref_count: int


def _marker_counts(
    check: SurfaceExpectation,
    observations: tuple[Observation, ...],
    arm: ArmResult,
    rubric: Mapping[str, str],
    counts: dict[str, list[int]],
) -> None:
    requests = tuple(
        item.request
        for item in arm.observations
        if item.action_id == check.action_id and item.request is not None
    )
    user_markers = frozenset(
        marker for request in requests for marker in request.user_origin_markers
    )
    forbidden_markers = {
        value for key, value in rubric.items() if key.split(".", 1)[0] in check.forbidden_documents
    }
    marker_metric = (
        "model_output_marker_rate" if check.surface == "model_context" else "released_marker_rate"
    )
    for item in observations:
        if check.surface == "model_context" and item.request is None:
            continue
        eligible = forbidden_markers - user_markers
        if eligible:
            counts[marker_metric][0] += any(marker in item.body for marker in eligible)
            counts[marker_metric][1] += 1
        if user_markers:
            counts["user_origin_marker_output_rate"][0] += any(
                marker in item.body for marker in user_markers
            )
            counts["user_origin_marker_output_rate"][1] += 1


def summarize_arm(case: Scenario, arm: ArmResult, rubric: Mapping[str, str]) -> ArmMetrics:
    """Summarize complete logical surfaces against an explicit, verified fact rubric."""
    expected_keys = {(check.action_id, check.surface) for check in case.expected.checks}
    actual_keys = {(item.action_id, item.surface) for item in arm.observations}
    if expected_keys != actual_keys or len(expected_keys) != len(case.expected.checks):
        message = "Missing, unexpected or duplicate expected surface"
        raise ValueError(message)
    counts = {
        name: [0, 0]
        for name in (
            "system_context_violation_rate",
            "release_violation_rate",
            "stored_read_violation_rate",
            "source_read_violation_rate",
            "document_list_violation_rate",
            "surface_conformance_rate",
            "surface_violation_rate",
            "false_denial_rate",
            "allowed_utility",
            "fully_correct_answer_rate",
            "model_output_marker_rate",
            "released_marker_rate",
            "user_origin_marker_output_rate",
            "revocation_success_rate",
        )
    }
    documents = dict(arm.documents)
    all_conform = True
    forbidden_ref_count = 0
    for check in case.expected.checks:
        observations = tuple(
            item
            for item in arm.observations
            if (item.action_id, item.surface) == (check.action_id, check.surface)
        )
        if check.surface != "model_context" and len(observations) != 1:
            message = "Duplicate logical surface evidence"
            raise ValueError(message)
        judgement = judge_surface(check, observations, arm)
        all_conform &= judgement.conforms
        forbidden_ref_count += judgement.forbidden_ref_count
        counts["surface_conformance_rate"][0] += judgement.conforms
        counts["surface_conformance_rate"][1] += 1
        counts["surface_violation_rate"][0] += judgement.violation
        counts["surface_violation_rate"][1] += 1
        name = (
            "system_context" if check.surface == "model_context" else check.surface
        ) + "_violation_rate"
        eligible = (
            check.authorization != "allow"
            if check.surface == "release"
            else bool(check.forbidden_documents) or check.authorization != "allow"
        )
        if name in counts and eligible:
            counts[name][0] += judgement.violation
            counts[name][1] += 1
        if (
            check.surface == "release"
            and check.authorization == "allow"
            and check.allowed_fact_keys
        ):
            last = observations[-1]
            denied = last.persisted != "available" or not last.body
            counts["false_denial_rate"][0] += denied
            counts["false_denial_rate"][1] += 1
            correct = 0
            for fact in check.allowed_fact_keys:
                if fact not in rubric or not rubric[fact]:
                    message = "Missing verified fact rubric"
                    raise ValueError(message)
                document = documents[fact.split(".", 1)[0]]
                correct += (
                    not denied
                    and rubric[fact] in last.body
                    and any(ref.document_id == document for ref in last.source_refs)
                )
            counts["allowed_utility"][0] += correct
            counts["allowed_utility"][1] += len(check.allowed_fact_keys)
            counts["fully_correct_answer_rate"][0] += correct == len(check.allowed_fact_keys)
            counts["fully_correct_answer_rate"][1] += 1
        if check.surface in {"model_context", "release"}:
            _marker_counts(check, observations, arm, rubric, counts)
    if any(
        action.kind in {"revoke_direct", "revoke_role", "remove_membership", "replace_version"}
        for action in case.actions
    ):
        counts["revocation_success_rate"] = [int(all_conform), 1]
    return ArmMetrics(
        tuple((name, make_rate(*values)) for name, values in counts.items()), forbidden_ref_count
    )
