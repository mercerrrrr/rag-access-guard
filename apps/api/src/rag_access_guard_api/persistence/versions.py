"""Immutable source bytes and canonical text of stored document versions."""

from datetime import datetime
from typing import ClassVar
from uuid import UUID, uuid4

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_as_dataclass, mapped_column

from rag_access_guard_api.persistence.base import Base


@mapped_as_dataclass(Base.registry, kw_only=True)
class DocumentVersion:
    """Insertion-only records; PostgreSQL rejects later mutation or deletion."""

    __tablename__: ClassVar[str] = "document_versions"
    __table_args__: ClassVar[tuple[CheckConstraint | UniqueConstraint, ...]] = (
        UniqueConstraint("document_id", "id", name="uq_document_versions_document_id_id"),
        CheckConstraint(
            "byte_size > 0 AND byte_size <= 1048576 AND byte_size = octet_length(original_bytes)",
            name="ck_document_versions_byte_size",
        ),
        CheckConstraint(
            """content_sha256 ~ '^[0-9a-f]{64}$'
            AND content_sha256 = encode(sha256(original_bytes), 'hex')""",
            name="ck_document_versions_content_hash",
        ),
        CheckConstraint(
            """text_sha256 ~ '^[0-9a-f]{64}$'
            AND text_sha256 = encode(sha256(convert_to(extracted_text, 'UTF8')), 'hex')""",
            name="ck_document_versions_text_hash",
        ),
        CheckConstraint(
            "extracted_text ~ '[^[:space:]]'", name="ck_document_versions_text_nonblank"
        ),
        CheckConstraint("media_type = 'text/plain'", name="ck_document_versions_media_type"),
        CheckConstraint(
            "parser_revision = 'utf8-text-v1'", name="ck_document_versions_parser_revision"
        ),
        CheckConstraint("status = 'stored'", name="ck_document_versions_status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default_factory=uuid4)
    document_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("documents.id", ondelete="RESTRICT"))
    original_bytes: Mapped[bytes] = mapped_column(LargeBinary, repr=False)
    content_sha256: Mapped[str] = mapped_column(CHAR(64))
    extracted_text: Mapped[str] = mapped_column(Text, repr=False)
    text_sha256: Mapped[str] = mapped_column(CHAR(64))
    media_type: Mapped[str] = mapped_column(String(64))
    byte_size: Mapped[int] = mapped_column(BigInteger)
    parser_revision: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), init=False
    )
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="RESTRICT"))
