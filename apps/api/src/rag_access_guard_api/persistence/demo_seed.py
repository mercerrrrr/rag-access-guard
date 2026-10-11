"""Ownership singleton and completed logical steps outside production policy state."""

from typing import ClassVar
from uuid import UUID

from sqlalchemy import CHAR, CheckConstraint, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_as_dataclass, mapped_column

from rag_access_guard_api.persistence.base import Base


@mapped_as_dataclass(Base.registry, kw_only=True)
class DemoSeedState:
    """Mutable ORM representation; marker identity is fixed at bootstrap."""

    __tablename__: ClassVar[str] = "demo_seed_state"
    __table_args__: ClassVar[tuple[CheckConstraint, ...]] = (
        CheckConstraint("id = 1", name="ck_demo_seed_state_singleton"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    instance_id: Mapped[UUID] = mapped_column(Uuid, unique=True)
    dataset_id: Mapped[str] = mapped_column(String(100))
    database_name: Mapped[str] = mapped_column(String(63))
    database_role: Mapped[str] = mapped_column(String(63))


@mapped_as_dataclass(Base.registry, kw_only=True)
class DemoSeedStep:
    """Every object and its completed step commit in the same transaction."""

    __tablename__: ClassVar[str] = "demo_seed_steps"
    instance_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("demo_seed_state.instance_id", ondelete="RESTRICT"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(512), primary_key=True)
    object_id: Mapped[UUID] = mapped_column(Uuid)
    version_id: Mapped[UUID | None] = mapped_column(Uuid, default=None)
    upload_sha256: Mapped[str | None] = mapped_column(CHAR(64), default=None)
    origin_sha256: Mapped[str | None] = mapped_column(CHAR(64), default=None)
    definition_sha256: Mapped[str | None] = mapped_column(CHAR(64), default=None)
