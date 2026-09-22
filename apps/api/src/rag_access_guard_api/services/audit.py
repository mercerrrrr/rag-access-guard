"""Transactional, content-free audit writing."""

from dataclasses import asdict, dataclass
from uuid import UUID, uuid4

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from rag_access_guard_api.persistence.policy import (
    AuditEvent,
    AuditEventType,
    AuditOutcome,
    AuditStage,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class AuditRecord:
    """Closed audit vocabulary and identifiers, with no free-text payload."""

    event_type: AuditEventType
    stage: AuditStage
    outcome: AuditOutcome
    actor_user_id: UUID | None = None
    principal_id: UUID | None = None
    document_id: UUID | None = None
    role_id: UUID | None = None
    grant_id: UUID | None = None
    source_count: int = 0


async def write_audit(connection: AsyncConnection, event: AuditRecord, revision: int) -> None:
    """Insert using the caller's security transaction, propagating failure."""
    _ = await connection.execute(
        insert(AuditEvent).values(id=uuid4(), policy_revision=revision, **asdict(event))
    )
