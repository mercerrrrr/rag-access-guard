"""Logical documents and explicit allow grants."""

from datetime import datetime
from typing import ClassVar
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_as_dataclass, mapped_column

from rag_access_guard_api.persistence.base import Base


@mapped_as_dataclass(Base.registry, kw_only=True)
class Document:
    """Logical document identity; content and versions are stored separately."""

    __tablename__: ClassVar[str] = "documents"
    __table_args__: ClassVar[tuple[CheckConstraint, ...]] = (
        CheckConstraint("length(btrim(title)) > 0", name="ck_documents_title_nonblank"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default_factory=uuid4)
    title: Mapped[str] = mapped_column(String(512))
    created_by: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), init=False
    )
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))


@mapped_as_dataclass(Base.registry, kw_only=True)
class DocumentGrant:
    """Independent allow grant to exactly one user or role."""

    __tablename__: ClassVar[str] = "document_grants"
    __table_args__: ClassVar[tuple[CheckConstraint | Index, ...]] = (
        CheckConstraint(
            "(user_id IS NULL) <> (role_id IS NULL)", name="ck_document_grants_exactly_one_subject"
        ),
        Index(
            "uq_document_grants_document_user",
            "document_id",
            "user_id",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
        Index(
            "uq_document_grants_document_role",
            "document_id",
            "role_id",
            unique=True,
            postgresql_where=text("role_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default_factory=uuid4)
    document_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("documents.id", ondelete="RESTRICT"))
    user_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), index=True, default=None
    )
    role_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("roles.id", ondelete="RESTRICT"), index=True, default=None
    )
    created_by: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), init=False
    )
