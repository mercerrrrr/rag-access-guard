"""Closed audit metadata and bounded keyset query contracts."""

from base64 import b64decode, urlsafe_b64encode
from datetime import datetime
from typing import Annotated, ClassVar, Self, assert_never
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from rag_access_guard_api.persistence.policy import AuditEventType, AuditOutcome, AuditStage


class AuditView(BaseModel):
    """Identifiers and closed codes only, never credentials or document content."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    occurred_at: datetime
    actor_user_id: UUID | None
    principal_id: UUID | None
    document_id: UUID | None
    role_id: UUID | None
    grant_id: UUID | None
    event_type: AuditEventType
    stage: AuditStage
    outcome: AuditOutcome
    policy_revision: int
    source_count: int


class AuditPage(BaseModel):
    """A bounded page; the cursor carries no authorization."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    items: tuple[AuditView, ...]
    next_cursor: str | None


class AuditCursor(BaseModel):
    """Validated position in the descending timestamp/UUID ordering."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    occurred_at: AwareDatetime
    id: UUID


def decode_cursor(encoded: str | None) -> AuditCursor | None:
    """Parse an untrusted cursor before it reaches the database query."""
    if encoded is None:
        return None
    timestamp, identifier = (
        b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        .decode("ascii")
        .split("|")
    )
    return AuditCursor(occurred_at=datetime.fromisoformat(timestamp), id=UUID(identifier))


def encode_cursor(entry: AuditView) -> str:
    """Encode both ordering keys so equal timestamps cannot duplicate rows."""
    value = f"{entry.occurred_at.isoformat()}|{entry.id}"
    return urlsafe_b64encode(value.encode("ascii")).decode("ascii").rstrip("=")


class AuditQuery(BaseModel):
    """Allowlisted filters; timestamps form an inclusive/exclusive interval."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    event_type: AuditEventType | None = None
    stage: AuditStage | None = None
    outcome: AuditOutcome | None = None
    since: AwareDatetime | None = None
    until: AwareDatetime | None = None
    limit: Annotated[int, Field(ge=1, le=100)] = 50
    cursor: Annotated[str, Field(min_length=1, max_length=256)] | None = None

    @field_validator("since", "until", mode="before")
    @classmethod
    def iso_timestamp(cls, value: str | datetime | None) -> datetime | None:
        """Require ISO timestamps rather than implicit epoch numbers."""
        match value:
            case str():
                return datetime.fromisoformat(value)
            case datetime() | None:
                return value
            case _:
                assert_never(value)

    @model_validator(mode="after")
    def ordered_interval(self) -> Self:
        """Reject empty or reversed ranges at the HTTP boundary."""
        if self.since is not None and self.until is not None and self.since >= self.until:
            message = "since must precede until"
            raise ValueError(message)
        return self
