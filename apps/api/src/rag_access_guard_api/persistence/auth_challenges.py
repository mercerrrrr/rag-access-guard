"""One-use pre-authentication challenge records."""

from datetime import datetime
from typing import ClassVar
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, LargeBinary, Uuid
from sqlalchemy.orm import Mapped, mapped_as_dataclass, mapped_column

from rag_access_guard_api.persistence.base import Base


@mapped_as_dataclass(Base.registry, kw_only=True)
class AuthChallenge:
    """Mutable challenge state; only digests enter persistence."""

    __tablename__: ClassVar[str] = "auth_challenges"
    __table_args__: ClassVar[tuple[CheckConstraint, ...]] = (
        CheckConstraint("octet_length(token_digest) = 32", name="ck_auth_challenges_token_length"),
        CheckConstraint(
            "octet_length(csrf_token_digest) = 32", name="ck_auth_challenges_csrf_length"
        ),
        CheckConstraint(
            "expires_at = created_at + interval '10 minutes'", name="ck_auth_challenges_expiry"
        ),
        CheckConstraint("consumed_at >= created_at", name="ck_auth_challenges_consumed_order"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default_factory=uuid4)
    token_digest: Mapped[bytes] = mapped_column(LargeBinary, unique=True, repr=False)
    csrf_token_digest: Mapped[bytes] = mapped_column(LargeBinary, repr=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
