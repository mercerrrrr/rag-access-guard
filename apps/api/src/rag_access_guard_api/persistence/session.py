"""Server-side session records without raw tokens."""

from datetime import datetime
from typing import ClassVar
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, LargeBinary, Uuid, func
from sqlalchemy.orm import Mapped, mapped_as_dataclass, mapped_column

from rag_access_guard_api.persistence.base import Base


@mapped_as_dataclass(Base.registry, kw_only=True)
class Session:
    """Session state whose digests and timestamps are updated by the server."""

    __tablename__: ClassVar[str] = "sessions"
    __table_args__: ClassVar[tuple[CheckConstraint, ...]] = (
        CheckConstraint("octet_length(token_digest) = 32", name="ck_sessions_token_digest_length"),
        CheckConstraint(
            "octet_length(csrf_token_digest) = 32", name="ck_sessions_csrf_token_digest_length"
        ),
        CheckConstraint("last_seen_at >= created_at", name="ck_sessions_last_seen_order"),
        CheckConstraint("last_seen_at < absolute_expires_at", name="ck_sessions_expiry_order"),
        CheckConstraint("revoked_at >= created_at", name="ck_sessions_revoked_order"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default_factory=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    token_digest: Mapped[bytes] = mapped_column(LargeBinary, unique=True, repr=False)
    csrf_token_digest: Mapped[bytes] = mapped_column(LargeBinary, unique=True, repr=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), init=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), init=False
    )
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
