"""Session identities and closed authentication request/response shapes."""

from dataclasses import dataclass
from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from rag_access_guard_api.services.passwords import MAX_PASSWORD_BYTES


@dataclass(frozen=True, slots=True)
class SessionPrincipal:
    """Identity granted only by a fresh server session gate."""

    principal_id: UUID
    session_id: UUID
    is_admin: bool


class UserView(BaseModel):
    """Public account metadata, never password material."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    login: str
    display_name: str
    is_admin: bool


class LoginRequest(BaseModel):
    """Bounded login credentials; password bytes are never normalized."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    login: str = Field(min_length=1, max_length=254)
    password: str = Field(repr=False, max_length=1024)

    @field_validator("login")
    @classmethod
    def normalize_login(cls, value: str) -> str:
        """Apply the canonical account identifier normalization."""
        return value.strip().lower()

    @field_validator("password")
    @classmethod
    def bound_password(cls, value: str) -> str:
        """Reject oversized UTF-8 inputs before hashing."""
        if len(value.encode("utf-8")) > MAX_PASSWORD_BYTES:
            message = "Password exceeds byte limit"
            raise ValueError(message)
        return value


class CsrfResponse(BaseModel):
    """Synchronizer token delivered only through the authentication protocol."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    csrf_token: str


class UserResponse(BaseModel):
    """Restored identity without session credentials."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    user: UserView


class LoginResponse(UserResponse):
    """New identity and its independent synchronizer token."""

    csrf_token: str
