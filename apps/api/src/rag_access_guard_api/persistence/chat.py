"""Owned conversations and source-linked turn storage."""

from datetime import datetime
from typing import ClassVar
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_as_dataclass, mapped_column

from rag_access_guard_api.persistence.base import Base


@mapped_as_dataclass(Base.registry, kw_only=True)
class ChatThread:
    """Mutable title and revision with a database-enforced immutable owner."""

    __tablename__: ClassVar[str] = "chat_threads"
    __table_args__: ClassVar[tuple[CheckConstraint | Index, ...]] = (
        CheckConstraint("revision >= 0", name="ck_chat_threads_revision"),
        Index("ix_chat_threads_owner_created", "owner_user_id", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default_factory=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    title: Mapped[str] = mapped_column(Text, default="Новый диалог", server_default="Новый диалог")
    revision: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), init=False, server_default=text("clock_timestamp()")
    )


@mapped_as_dataclass(Base.registry, kw_only=True)
class ChatTurn:
    """A reserved question becomes an answered or server-neutral pair."""

    __tablename__: ClassVar[str] = "chat_turns"
    __table_args__: ClassVar[tuple[CheckConstraint | UniqueConstraint | Index, ...]] = (
        UniqueConstraint("thread_id", "request_id", name="uq_chat_turns_request"),
        UniqueConstraint("thread_id", "ordinal", name="uq_chat_turns_ordinal"),
        Index(
            "uq_chat_turns_pending",
            "thread_id",
            unique=True,
            postgresql_where=text("state = 'pending'"),
        ),
        CheckConstraint("octet_length(request_sha256) = 32", name="ck_chat_turns_request_hash"),
        CheckConstraint("expected_thread_revision >= 0", name="ck_chat_turns_revision"),
        CheckConstraint(
            """(
            (state='pending' AND answer IS NULL AND completed_at IS NULL AND ordinal IS NULL
             AND lease_expires_at IS NOT NULL AND NOT provenance_complete
             AND NOT server_generated_neutral AND neutral_reason IS NULL)
            OR (state='available' AND answer IS NOT NULL AND completed_at IS NOT NULL
             AND ordinal IS NOT NULL AND ordinal > 0 AND provenance_complete
             AND NOT server_generated_neutral AND neutral_reason IS NULL)
            OR (state='neutral' AND answer IS NULL AND completed_at IS NOT NULL
             AND ordinal IS NOT NULL AND ordinal > 0 AND NOT provenance_complete
             AND server_generated_neutral AND neutral_reason IN
             ('no_context','generation_unavailable','policy_changed','interrupted'))
            ) IS TRUE""",
            name="ck_chat_turns_state",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default_factory=uuid4)
    thread_id: Mapped[UUID] = mapped_column(ForeignKey("chat_threads.id", ondelete="RESTRICT"))
    request_id: Mapped[UUID] = mapped_column(Uuid)
    request_sha256: Mapped[bytes] = mapped_column(LargeBinary, repr=False)
    expected_thread_revision: Mapped[int] = mapped_column(BigInteger)
    user_input: Mapped[str] = mapped_column(Text, repr=False)
    state: Mapped[str] = mapped_column(Text, default="pending", server_default="pending")
    ordinal: Mapped[int | None] = mapped_column(BigInteger, default=None)
    answer: Mapped[str | None] = mapped_column(Text, default=None, repr=False)
    provenance_complete: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    server_generated_neutral: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    neutral_reason: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), init=False, server_default=text("clock_timestamp()")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


@mapped_as_dataclass(Base.registry, kw_only=True)
class TurnSource:
    """Canonical chunk witness for the answer, never a separate user-input source."""

    __tablename__: ClassVar[str] = "turn_sources"
    __table_args__: ClassVar[tuple[ForeignKeyConstraint, ...]] = (
        ForeignKeyConstraint(
            ["document_id", "document_version_id", "chunk_id"],
            [
                "document_chunks.document_id",
                "document_chunks.document_version_id",
                "document_chunks.id",
            ],
            name="fk_turn_sources_chunk",
            ondelete="RESTRICT",
        ),
    )
    turn_id: Mapped[UUID] = mapped_column(
        ForeignKey("chat_turns.id", ondelete="RESTRICT"), primary_key=True
    )
    document_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    document_version_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    chunk_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
