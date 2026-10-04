"""Admit sequential attempt timings only when captured call evidence agrees."""

from math import isclose

from experiments.observations import ArmResult, AttemptEvidence, Observation


def _validate_action(
    attempts: tuple[AttemptEvidence, ...], observations: tuple[Observation, ...]
) -> None:
    if tuple(attempt.number for attempt in attempts) != tuple(range(1, len(attempts) + 1)):
        message = "Noncontiguous timing attempts"
        raise ValueError(message)
    if (
        any(attempt.status != "stale_discarded" for attempt in attempts[:-1])
        or attempts[-1].status == "stale_discarded"
    ):
        message = "Invalid timing terminal status"
        raise ValueError(message)
    for attempt in attempts:
        contexts = tuple(
            item
            for item in observations
            if item.surface == "model_context" and item.attempt_number == attempt.number
        )
        if (
            len(contexts) > 1
            or attempt.model_called != any(item.request is not None for item in contexts)
            or (not contexts and attempt.status != "failed")
        ):
            message = "Missing or contradictory model timing evidence"
            raise ValueError(message)
        if any(
            item.attempt_status != attempt.status
            or item.attempt_elapsed_ms is None
            or not isclose(item.attempt_elapsed_ms, attempt.elapsed_ms, abs_tol=0.001)
            for item in contexts
        ):
            message = "Model observation timing differs from attempt"
            raise ValueError(message)
    releases = tuple(item for item in observations if item.surface == "release")
    if len(releases) != 1 or releases[0].http_elapsed_ms is None:
        message = "Missing logical HTTP timing"
        raise ValueError(message)
    if releases[0].http_elapsed_ms + 1.0 < sum(attempt.elapsed_ms for attempt in attempts):
        message = "Logical timing is shorter than sequential attempts"
        raise ValueError(message)
    last = attempts[-1]
    release = releases[0]
    if not (last.status == "failed" and release.attempt_number is None) and (
        release.attempt_number != last.number
        or release.attempt_status != last.status
        or release.attempt_elapsed_ms is None
        or not isclose(release.attempt_elapsed_ms, last.elapsed_ms, abs_tol=0.001)
    ):
        message = "Terminal release timing does not match final attempt"
        raise ValueError(message)


def validate_timings(arm: ArmResult) -> None:
    """Allow 1 ms transport rounding tolerance, not omitted retries or model calls."""
    keys = {(attempt.action_id, attempt.number) for attempt in arm.attempts}
    if len(keys) != len(arm.attempts) or any(
        (item.action_id, item.attempt_number) not in keys
        for item in arm.observations
        if item.surface == "model_context" or item.attempt_number is not None
    ):
        message = "Missing or duplicate timing attempt evidence"
        raise ValueError(message)
    for action in dict.fromkeys(attempt.action_id for attempt in arm.attempts):
        attempts = tuple(attempt for attempt in arm.attempts if attempt.action_id == action)
        observations = tuple(item for item in arm.observations if item.action_id == action)
        _validate_action(attempts, observations)
