"""Security revision and content-free audit records."""

from datetime import datetime
from typing import ClassVar, Literal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    SmallInteger,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_as_dataclass, mapped_column

from rag_access_guard_api.persistence.base import Base

type AuditEventType = Literal[
    "session_created",
    "session_revoked",
    "login_denied",
    "user_changed",
    "role_changed",
    "membership_added",
    "membership_removed",
    "document_changed",
    "grant_added",
    "grant_removed",
    "access_checked",
]
type AuditStage = Literal["authentication", "policy", "retrieval", "context", "release", "read"]
type AuditOutcome = Literal["allowed", "denied", "success", "failure"]


@mapped_as_dataclass(Base.registry, kw_only=True)
class PolicyState:
    """Singleton revision record locked by policy changes and protected release."""

    __tablename__: ClassVar[str] = "policy_state"
    __table_args__: ClassVar[tuple[CheckConstraint, ...]] = (
        CheckConstraint("id = 1", name="ck_policy_state_singleton"),
        CheckConstraint("revision >= 0", name="ck_policy_state_revision_nonnegative"),
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    revision: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), init=False
    )


@mapped_as_dataclass(Base.registry, kw_only=True)
class AuditEvent:
    """Structured event with historical resource IDs and no protected content."""

    __tablename__: ClassVar[str] = "audit_events"
    __table_args__: ClassVar[tuple[CheckConstraint, ...]] = (
        CheckConstraint(
            """event_type IN (
                'session_created', 'session_revoked', 'login_denied',
                'user_changed', 'role_changed', 'membership_added', 'membership_removed',
                'document_changed', 'grant_added', 'grant_removed', 'access_checked'
            )""",
            name="ck_audit_events_event_type",
        ),
        CheckConstraint(
            "stage IN ('authentication', 'policy', 'retrieval', 'context', 'release', 'read')",
            name="ck_audit_events_stage",
        ),
        CheckConstraint(
            "outcome IN ('allowed', 'denied', 'success', 'failure')", name="ck_audit_events_outcome"
        ),
        CheckConstraint("policy_revision >= 0", name="ck_audit_events_policy_revision_nonnegative"),
        CheckConstraint("source_count >= 0", name="ck_audit_events_source_count_nonnegative"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default_factory=uuid4)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), init=False, index=True
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), default=None, index=True
    )
    principal_id: Mapped[UUID | None] = mapped_column(Uuid, default=None)
    document_id: Mapped[UUID | None] = mapped_column(Uuid, default=None)
    role_id: Mapped[UUID | None] = mapped_column(Uuid, default=None)
    grant_id: Mapped[UUID | None] = mapped_column(Uuid, default=None)
    event_type: Mapped[AuditEventType] = mapped_column(String(32))
    stage: Mapped[AuditStage] = mapped_column(String(16))
    outcome: Mapped[AuditOutcome] = mapped_column(String(16))
    policy_revision: Mapped[int] = mapped_column(BigInteger)
    source_count: Mapped[int] = mapped_column(default=0, server_default=text("0"))
