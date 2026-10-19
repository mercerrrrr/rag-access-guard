from datetime import UTC, datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

import pytest
from pydantic import ValidationError

from rag_access_guard_api.schemas.chat_progress import ProgressStage, RequestStatus
from rag_access_guard_api.services.chat_progress import (
    RequestProgressTracker,
    project_request_status,
)
from rag_access_guard_api.services.chat_turns import RequestMetadata


def test_terminal_status_has_only_content_free_fields() -> None:
    # Given: a terminal projection.
    dto = RequestStatus(request_id=UUID(int=1), state="terminal", stage=None, elapsed_seconds=None)
    # When: the DTO is serialized.
    result = dto.model_dump()
    # Then: no protected content can be attached.
    assert set(result) == {"request_id", "state", "stage", "elapsed_seconds"}
    with pytest.raises(ValidationError):
        _ = RequestStatus.model_validate({**result, "answer": "SECRET"})


@pytest.mark.parametrize("state", ["absent", "terminal", "expired"])
@pytest.mark.parametrize("field", ["stage", "elapsed_seconds"])
def test_nonrunning_status_rejects_live_progress(state: str, field: str) -> None:
    # Given: a persisted or absent outcome with an attached live field.
    payload = {
        "request_id": UUID(int=1),
        "state": state,
        "stage": "generation" if field == "stage" else None,
        "elapsed_seconds": 8 if field == "elapsed_seconds" else None,
    }
    # When/Then: constructing an impossible projection is denied.
    with pytest.raises(ValidationError):
        _ = RequestStatus.model_validate(payload)


@pytest.mark.parametrize("field", ["stage", "elapsed_seconds"])
def test_running_status_requires_stage_and_elapsed(field: str) -> None:
    # Given: an incomplete running projection.
    payload = {
        "request_id": UUID(int=1),
        "state": "running",
        "stage": "retrieval" if field != "stage" else None,
        "elapsed_seconds": 0 if field != "elapsed_seconds" else None,
    }
    # When/Then: a running state must carry both live fields.
    with pytest.raises(ValidationError):
        _ = RequestStatus.model_validate(payload)


@pytest.mark.parametrize("elapsed", [-1, 1.5, True, "3"])
def test_elapsed_requires_strict_nonnegative_integer(elapsed: float | str) -> None:
    # Given: malformed elapsed data.
    # When/Then: parsing does not coerce it into valid elapsed seconds.
    with pytest.raises(ValidationError):
        _ = RequestStatus.model_validate(
            {
                "request_id": UUID(int=1),
                "state": "running",
                "stage": "recovery",
                "elapsed_seconds": elapsed,
            }
        )


def test_status_is_frozen() -> None:
    # Given: a complete DTO.
    dto = RequestStatus(request_id=UUID(int=1), state="absent", stage=None, elapsed_seconds=None)
    # When/Then: persisted projection cannot be mutated later.
    with pytest.raises(ValidationError):
        dto.state = "terminal"


@pytest.mark.parametrize(
    ("state", "lease_delta", "stage", "expected_state", "expected_stage", "elapsed"),
    [
        (None, 10, "generation", "absent", None, None),
        ("available", 10, "generation", "terminal", None, None),
        ("neutral", 10, "generation", "terminal", None, None),
        ("pending", 0, "generation", "expired", None, None),
        ("pending", -1, "retrieval", "expired", None, None),
        ("pending", 10, "generation", "running", "generation", 8),
        ("pending", 10, None, "running", "recovery", 8),
    ],
)
def test_projection_persisted_state_wins_over_memory(  # noqa: PLR0913, PLR0917
    state: Literal["pending", "available", "neutral"] | None,
    lease_delta: int,
    stage: ProgressStage | None,
    expected_state: str,
    expected_stage: str | None,
    elapsed: int | None,
) -> None:
    # Given: an independent database snapshot and possibly stale observation.
    now = datetime(2026, 10, 20, tzinfo=UTC)
    # When: the pure projection sees that snapshot.
    result = project_request_status(
        UUID(int=1),
        state=state,
        lease_expires_at=now + timedelta(seconds=lease_delta),
        created_at=now - timedelta(seconds=8.9),
        now=now,
        stage=stage,
    )
    # Then: persisted completion/expiry beats any in-memory stage.
    assert (result.state, result.stage, result.elapsed_seconds) == (
        expected_state,
        expected_stage,
        elapsed,
    )


def test_negative_database_elapsed_is_clamped_to_zero() -> None:
    # Given: clock skew relative to a future row creation time.
    now = datetime(2026, 10, 20, tzinfo=UTC)
    # When: a live request is projected.
    result = project_request_status(
        UUID(int=1),
        state="pending",
        lease_expires_at=now + timedelta(seconds=10),
        created_at=now + timedelta(seconds=1),
        now=now,
        stage="retrieval",
    )
    # Then: elapsed never becomes negative.
    assert result.elapsed_seconds == 0


def test_tracker_is_scoped_by_thread_and_request_and_discards() -> None:
    # Given: two app trackers and two threads sharing a request UUID.
    tracker, other = RequestProgressTracker(), RequestProgressTracker()
    thread, foreign, request = UUID(int=1), UUID(int=2), UUID(int=3)
    # When: one app publishes and then discards its own observation.
    tracker.set_stage(thread, request, "retrieval")
    assert tracker.get_stage(thread, request) == "retrieval"
    assert tracker.get_stage(foreign, request) is None
    assert other.get_stage(thread, request) is None
    tracker.set_stage(thread, request, "authorization")
    assert tracker.get_stage(thread, request) == "authorization"
    tracker.discard(thread, request)
    tracker.discard(thread, request)
    # Then: no terminal cache or foreign entry remains.
    assert tracker.get_stage(thread, request) is None


@pytest.mark.parametrize("field", ["now", "created_at", "lease_expires_at"])
def test_projector_rejects_naive_datetime(field: str) -> None:
    # Given: one timestamp has no timezone proof.
    now = datetime(2026, 10, 20, tzinfo=UTC)
    naive = now.replace(tzinfo=None)
    # When/Then: malformed internal timestamps cannot silently affect lease decisions.
    with pytest.raises(ValueError, match="aware UTC"):
        _ = project_request_status(
            UUID(int=1),
            state="pending",
            lease_expires_at=naive if field == "lease_expires_at" else now + timedelta(seconds=10),
            created_at=naive if field == "created_at" else now,
            now=naive if field == "now" else now,
            stage=None,
        )


def test_database_metadata_normalizes_aware_timestamps_to_utc() -> None:
    # Given: PostgreSQL returns aware timestamps in the host's configured timezone.
    local = datetime(2026, 10, 20, 3, tzinfo=timezone(timedelta(hours=3)))
    # When: the request metadata crosses the database boundary.
    metadata = RequestMetadata(state="pending", created_at=local, lease_expires_at=local)
    # Then: the pure projector receives UTC without changing the represented instant.
    assert metadata.created_at.tzinfo is UTC
    assert metadata.created_at.hour == 0
    assert metadata.lease_expires_at is not None
    assert metadata.lease_expires_at.tzinfo is UTC
