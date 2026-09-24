"""Immutable source bytes and canonical text of stored document versions."""

from datetime import datetime
from typing import ClassVar
from uuid import UUID, uuid4

from pydantic import JsonValue
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_as_dataclass, mapped_column

from rag_access_guard_api.persistence.base import Base


@mapped_as_dataclass(Base.registry, kw_only=True)
class DocumentVersion:
    """Immutable artifacts with separately constrained processing status."""

    __tablename__: ClassVar[str] = "document_versions"
    __table_args__: ClassVar[tuple[CheckConstraint | UniqueConstraint, ...]] = (
        UniqueConstraint("document_id", "id", name="uq_document_versions_document_id_id"),
        CheckConstraint(
            "byte_size > 0 AND byte_size <= 10485760 AND byte_size = octet_length(original_bytes)",
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
        CheckConstraint(
            "media_type IN ('text/plain', 'text/markdown')", name="ck_document_versions_media_type"
        ),
        CheckConstraint(
            "parser_revision = 'utf8-text-v1'", name="ck_document_versions_parser_revision"
        ),
        CheckConstraint(
            "status IN ('stored', 'chunked', 'indexing', 'ready', 'failed')",
            name="ck_document_versions_status",
        ),
        CheckConstraint(
            """ingestion_manifest = jsonb_build_object(
            'schema_version', 1, 'source_sha256', content_sha256, 'text_sha256', text_sha256,
            'byte_size', byte_size, 'parser_revision', parser_revision,
            'chunker_revision', NULL, 'tokenizer_revision', NULL,
            'embedding_model_id', NULL, 'embedding_model_revision', NULL,
            'config_sha256', encode(sha256(convert_to(
                '{"parser_revision":"' || parser_revision || '"}', 'UTF8')), 'hex'))
            OR ingestion_manifest = jsonb_build_object(
            'schema_version', 1, 'source_sha256', content_sha256, 'text_sha256', text_sha256,
            'byte_size', byte_size, 'parser_revision', parser_revision,
            'chunker_revision', 'e5-window400-overlap50-offsets-v1',
            'tokenizer_revision', 'intfloat/multilingual-e5-small@'
                || '614241f622f53c4eeff9890bdc4f31cfecc418b3:content-no-special',
            'embedding_model_id', NULL, 'embedding_model_revision', NULL,
            'config_sha256', encode(sha256(convert_to(
                '{"chunker_revision":"e5-window400-overlap50-offsets-v1","parser_revision":"'
                || parser_revision || '","tokenizer_revision":"intfloat/multilingual-e5-small@'
                || '614241f622f53c4eeff9890bdc4f31cfecc418b3:content-no-special"}',
                'UTF8')), 'hex'))""",
            name="ck_document_versions_manifest",
        ),
        CheckConstraint(
            """(status = 'failed' AND failure_code IS NOT NULL AND failure_code IN (
                'unsupported_type', 'invalid_encoding', 'empty_text', 'size_limit',
                'parse_failed', 'index_failed'))
            OR (status <> 'failed' AND failure_code IS NULL)""",
            name="ck_document_versions_failure",
        ),
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
    ingestion_manifest: Mapped[dict[str, JsonValue]] = mapped_column(JSONB, repr=False)
    failure_code: Mapped[str | None] = mapped_column(String(32), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), init=False
    )
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="RESTRICT"))
