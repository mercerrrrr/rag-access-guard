"""Validate declared observations; these checks do not execute a security scenario."""

from typing import Final

from experiments.scenario_types import SurfaceExpectation

type Shape = tuple[str, int | None, str, str]

SHAPES: Final[dict[str, frozenset[Shape]]] = {
    "model_context": frozenset(
        {
            ("allow", None, "protected", "unchanged"),
            ("deny", None, "neutral", "unchanged"),
        }
    ),
    "release": frozenset(
        {
            ("allow", 200, "protected", "available"),
            ("neutral", 200, "neutral", "neutral"),
            *(("deny", status, "redacted", "unchanged") for status in (401, 403, 404, 503)),
        }
    ),
    "stored_read": frozenset(
        {
            ("allow", 200, "protected", "unchanged"),
            ("neutral", 200, "neutral", "unchanged"),
            *(("deny", status, "redacted", "unchanged") for status in (200, 401, 404, 503)),
        }
    ),
    "source_read": frozenset(
        {
            ("allow", 200, "protected", "unchanged"),
            *(("deny", status, "redacted", "unchanged") for status in (401, 404, 503)),
        }
    ),
    "document_list": frozenset(
        {
            ("allow", 200, "list", "unchanged"),
            ("deny", 200, "list", "unchanged"),
            *(("deny", status, "redacted", "unchanged") for status in (401, 503)),
        }
    ),
}


def validate_oracle(check: SurfaceExpectation) -> None:
    """Reject impossible channel, response and persistence combinations before execution."""
    shape = (check.authorization, check.http_status, check.body, check.persisted)
    calls_valid = check.llm_calls is None
    if check.surface == "model_context":
        calls_valid = (
            check.llm_calls is not None and check.llm_calls > 0
            if check.authorization == "allow"
            else check.llm_calls == 0
        )
    if shape not in SHAPES[check.surface] or not calls_valid:
        message = "inconsistent surface oracle"
        raise ValueError(message)
