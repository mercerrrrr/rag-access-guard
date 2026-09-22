"""Closed request and response shapes for document access."""

from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AccessibleDocument(BaseModel):
    """Metadata of a document the current session can read."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    title: str
    active_version_id: UUID


class AccessibleDocuments(BaseModel):
    """Stable title/id ordered document list."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    items: tuple[AccessibleDocument, ...]


class DocumentText(BaseModel):
    """Canonical stored text, not a chunk citation or original download."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    document_id: UUID
    document_version_id: UUID
    text: str


class DirectGrantRequest(BaseModel):
    """The administrator selects a recipient, not the acting principal."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    user_id: UUID


class GrantView(BaseModel):
    """Administrative metadata for one explicit allow path."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    document_id: UUID
    user_id: UUID | None
    role_id: UUID | None


class GrantList(BaseModel):
    """Explicit grants, visible only to administrators."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    items: tuple[GrantView, ...]


class UserSummary(BaseModel):
    """Subject selection metadata without credentials or sessions."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    login: str
    display_name: str
    is_active: bool
    is_admin: bool


class UserList(BaseModel):
    """Available administrative grant subjects."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    items: tuple[UserSummary, ...]
