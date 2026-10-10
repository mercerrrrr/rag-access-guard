"""Append-only publisher metadata bound to an immutable document version."""

from typing import ClassVar
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import CHAR, CheckConstraint, ForeignKey, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_as_dataclass, mapped_column

from rag_access_guard_api.persistence.base import Base


@mapped_as_dataclass(Base.registry, kw_only=True)
class DocumentOriginRecord:
    """Persistence is mutable for SQLAlchemy; database triggers forbid row mutation."""

    __tablename__: ClassVar[str] = "document_origins"
    __table_args__: ClassVar[tuple[CheckConstraint, ...]] = (
        CheckConstraint("origin_sha256 ~ '^[0-9a-f]{64}$'", name="ck_document_origins_hash"),
        CheckConstraint("jsonb_typeof(origin) = 'object'", name="ck_document_origins_object"),
    )
    version_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("document_versions.id", ondelete="RESTRICT"), primary_key=True
    )
    origin: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    origin_sha256: Mapped[str] = mapped_column(CHAR(64))
