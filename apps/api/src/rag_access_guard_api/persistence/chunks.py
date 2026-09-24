"""Immutable canonical fragments owned by one document version."""

from typing import ClassVar
from uuid import UUID, uuid4

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    ForeignKeyConstraint,
    Integer,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_as_dataclass, mapped_column

from rag_access_guard_api.persistence.base import Base


@mapped_as_dataclass(Base.registry, kw_only=True)
class DocumentChunk:
    """Persist exact offsets and hashes; source identity is the complete tuple."""

    __tablename__: ClassVar[str] = "document_chunks"
    __table_args__: ClassVar[
        tuple[CheckConstraint | UniqueConstraint | ForeignKeyConstraint, ...]
    ] = (
        UniqueConstraint(
            "document_version_id", "ordinal", name="uq_document_chunks_version_ordinal"
        ),
        UniqueConstraint(
            "document_id", "document_version_id", "id", name="uq_document_chunks_source"
        ),
        ForeignKeyConstraint(
            ["document_id", "document_version_id"],
            ["document_versions.document_id", "document_versions.id"],
            name="fk_document_chunks_version",
            ondelete="RESTRICT",
        ),
        CheckConstraint("ordinal >= 0", name="ck_document_chunks_ordinal"),
        CheckConstraint("token_count BETWEEN 1 AND 400", name="ck_document_chunks_tokens"),
        CheckConstraint(
            """char_start >= 0 AND char_end > char_start
            AND char_end - char_start = char_length(text)""",
            name="ck_document_chunks_offsets",
        ),
        CheckConstraint(
            """content_sha256 ~ '^[0-9a-f]{64}$'
            AND content_sha256 = encode(sha256(convert_to(text, 'UTF8')), 'hex')""",
            name="ck_document_chunks_hash",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default_factory=uuid4)
    document_id: Mapped[UUID] = mapped_column(Uuid)
    document_version_id: Mapped[UUID] = mapped_column(Uuid)
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text, repr=False)
    content_sha256: Mapped[str] = mapped_column(CHAR(64))
    token_count: Mapped[int] = mapped_column(Integer)
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
