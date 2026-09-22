"""Bounded authentication counters without raw peer or account identifiers."""

from datetime import datetime
from typing import ClassVar

from sqlalchemy import CheckConstraint, DateTime, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_as_dataclass, mapped_column

from rag_access_guard_api.persistence.base import Base


@mapped_as_dataclass(Base.registry, kw_only=True)
class AuthRateBucket:
    """Mutable fixed-window counter reserved atomically by PostgreSQL."""

    __tablename__: ClassVar[str] = "auth_rate_buckets"
    __table_args__: ClassVar[tuple[CheckConstraint, ...]] = (
        CheckConstraint("octet_length(key_digest) = 32", name="ck_auth_rate_buckets_key_length"),
        CheckConstraint("attempts >= 1", name="ck_auth_rate_buckets_attempts_positive"),
    )
    kind: Mapped[str] = mapped_column(String(24), primary_key=True)
    key_digest: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True, repr=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    attempts: Mapped[int] = mapped_column()
