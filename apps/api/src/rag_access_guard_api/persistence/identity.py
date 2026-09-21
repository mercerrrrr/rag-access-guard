"""User and role database records."""

from datetime import datetime
from typing import ClassVar
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_as_dataclass, mapped_column

from rag_access_guard_api.persistence.base import Base


@mapped_as_dataclass(Base.registry, kw_only=True)
class User:
    """Account record; administrative status grants no document access."""

    __tablename__: ClassVar[str] = "users"
    __table_args__: ClassVar[tuple[CheckConstraint, ...]] = (
        CheckConstraint(
            "login = lower(btrim(login)) AND length(login) > 0",
            name="ck_users_login_normalized",
        ),
        CheckConstraint("length(btrim(display_name)) > 0", name="ck_users_display_name_nonblank"),
        CheckConstraint("length(btrim(password_hash)) > 0", name="ck_users_password_hash_nonblank"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default_factory=uuid4)
    login: Mapped[str] = mapped_column(String(254), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(Text, repr=False)
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    is_admin: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), init=False
    )


@mapped_as_dataclass(Base.registry, kw_only=True)
class Role:
    """Named role used by explicit membership and document grants."""

    __tablename__: ClassVar[str] = "roles"
    __table_args__: ClassVar[tuple[CheckConstraint, ...]] = (
        CheckConstraint("code ~ '^[a-z][a-z0-9_]{0,63}$'", name="ck_roles_code_format"),
        CheckConstraint("length(btrim(display_name)) > 0", name="ck_roles_display_name_nonblank"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default_factory=uuid4)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), init=False
    )


@mapped_as_dataclass(Base.registry, kw_only=True)
class UserRole:
    """Explicit membership without cascading permission changes."""

    __tablename__: ClassVar[str] = "user_roles"

    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True
    )
    role_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("roles.id", ondelete="RESTRICT"), primary_key=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), init=False
    )
