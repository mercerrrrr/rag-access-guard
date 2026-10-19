"""Ephemeral observations projected beneath persisted state and authorization."""

from datetime import datetime, timedelta
from typing import Literal, assert_never
from uuid import UUID

from rag_access_guard_api.schemas.chat_progress import ProgressStage, RequestStatus


class RequestProgressTracker:
    """App-owned mutable stages only; never ownership, content or completion."""

    def __init__(self) -> None:
        """Allocate this app's admitted-operation observations."""
        self._stages: dict[tuple[UUID, UUID], ProgressStage] = {}

    def set_stage(self, thread_id: UUID, request_id: UUID, stage: ProgressStage) -> None:
        """Publish an observation for an admitted reservation."""
        self._stages[thread_id, request_id] = stage

    def get_stage(self, thread_id: UUID, request_id: UUID) -> ProgressStage | None:
        """Read an observation only after database ownership checks."""
        return self._stages.get((thread_id, request_id))

    def discard(self, thread_id: UUID, request_id: UUID) -> None:
        """Remove an observation when its operation exits."""
        _ = self._stages.pop((thread_id, request_id), None)


def project_request_status(  # noqa: PLR0913 -- explicit phase contract and pure projection inputs.
    request_id: UUID,
    *,
    state: Literal["pending", "available", "neutral"] | None,
    lease_expires_at: datetime | None,
    created_at: datetime | None,
    now: datetime,
    stage: ProgressStage | None,
) -> RequestStatus:
    """Project persisted authority before consulting an ephemeral observation."""
    for timestamp in (now, created_at, lease_expires_at):
        if timestamp is not None and timestamp.utcoffset() != timedelta(0):
            message = "Request status timestamps must be aware UTC"
            raise ValueError(message)
    match state:
        case None:
            public_state = "absent"
        case "available" | "neutral":
            public_state = "terminal"
        case "pending":
            if lease_expires_at is None or created_at is None:
                message = "Pending request requires creation and lease timestamps"
                raise ValueError(message)
            if now < lease_expires_at:
                return RequestStatus(
                    request_id=request_id,
                    state="running",
                    stage=stage or "recovery",
                    elapsed_seconds=max(0, int((now - created_at).total_seconds())),
                )
            public_state = "expired"
        case _:
            assert_never(state)
    return RequestStatus(
        request_id=request_id, state=public_state, stage=None, elapsed_seconds=None
    )
