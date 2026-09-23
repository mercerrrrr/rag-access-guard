"""Administrative audit projection on the current protected transaction."""

from sqlalchemy import literal, select, tuple_

from rag_access_guard_api.persistence import AuditEvent
from rag_access_guard_api.schemas.audit import (
    AuditCursor,
    AuditPage,
    AuditQuery,
    AuditView,
    encode_cursor,
)
from rag_access_guard_api.services.documents import require_admin
from rag_access_guard_api.services.security import ReadUoW


async def read_audit(uow: ReadUoW, query: AuditQuery, cursor: AuditCursor | None) -> AuditPage:
    """Reauthorize every page and select only the public metadata columns."""
    require_admin(uow)
    statement = select(
        AuditEvent.id,
        AuditEvent.occurred_at,
        AuditEvent.actor_user_id,
        AuditEvent.principal_id,
        AuditEvent.document_id,
        AuditEvent.role_id,
        AuditEvent.grant_id,
        AuditEvent.event_type,
        AuditEvent.stage,
        AuditEvent.outcome,
        AuditEvent.policy_revision,
        AuditEvent.source_count,
    )
    if query.event_type is not None:
        statement = statement.where(AuditEvent.event_type == query.event_type)
    if query.stage is not None:
        statement = statement.where(AuditEvent.stage == query.stage)
    if query.outcome is not None:
        statement = statement.where(AuditEvent.outcome == query.outcome)
    if query.since is not None:
        statement = statement.where(AuditEvent.occurred_at >= query.since)
    if query.until is not None:
        statement = statement.where(AuditEvent.occurred_at < query.until)
    if cursor is not None:
        statement = statement.where(
            tuple_(AuditEvent.occurred_at, AuditEvent.id)
            < tuple_(literal(cursor.occurred_at), literal(cursor.id))
        )
    rows = (
        await uow.connection.execute(
            statement.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc()).limit(
                query.limit + 1
            )
        )
    ).mappings()
    entries = tuple(AuditView.model_validate(row) for row in rows)
    items = entries[: query.limit]
    return AuditPage(
        items=items,
        next_cursor=encode_cursor(items[-1]) if len(entries) > query.limit else None,
    )
