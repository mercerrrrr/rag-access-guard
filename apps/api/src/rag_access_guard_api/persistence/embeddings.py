"""Immutable vectors belonging to canonical document chunks."""

from datetime import datetime
from typing import ClassVar
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import CHAR, CheckConstraint, DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_as_dataclass, mapped_column

from rag_access_guard_api.persistence.base import Base


@mapped_as_dataclass(Base.registry, kw_only=True)
class ChunkEmbedding:
    """Persist one normalized vector under its immutable model identity."""

    __tablename__: ClassVar[str] = "chunk_embeddings"
    __table_args__: ClassVar[tuple[CheckConstraint, ...]] = (
        CheckConstraint(
            "abs(vector_norm(embedding) - 1) <= 0.00001", name="ck_chunk_embeddings_norm"
        ),
        CheckConstraint("model_revision ~ '^[0-9a-f]{40}$'", name="ck_chunk_embeddings_revision"),
    )

    chunk_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("document_chunks.id", ondelete="RESTRICT"), primary_key=True
    )
    embedding: Mapped[list[float]] = mapped_column(Vector(384), repr=False)
    model_id: Mapped[str] = mapped_column(String(200))
    model_revision: Mapped[str] = mapped_column(CHAR(40))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp(), init=False
    )
