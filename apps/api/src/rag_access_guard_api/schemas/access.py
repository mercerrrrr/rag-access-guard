"""Closed request and response shapes for document access."""

from typing import Annotated, ClassVar, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StrictBool, StringConstraints, model_validator


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


class RoleGrantRequest(BaseModel):
    """A role is an alternative grant subject, never an acting identity."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    role_id: UUID


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


class RoleView(BaseModel):
    """Role metadata without implicit administrative capabilities."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    code: str
    display_name: str


class RoleList(BaseModel):
    """Roles ordered by immutable code and identifier."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    items: tuple[RoleView, ...]


type RoleDisplayName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200, pattern=r"^[^\x00]+$"),
]


class RoleCreate(BaseModel):
    """Validated immutable role code and display label."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    code: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    display_name: RoleDisplayName


class RolePatch(BaseModel):
    """Only the display label is mutable."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    display_name: RoleDisplayName


class UserSecurityPatch(BaseModel):
    """Explicit security flags only; omitted values leave current state intact."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    is_active: StrictBool | None = None
    is_admin: StrictBool | None = None

    @model_validator(mode="after")
    def require_flags(self) -> Self:
        """Reject empty patches and explicit nulls before the transaction starts."""
        if (
            not self.model_fields_set
            or ("is_active" in self.model_fields_set and self.is_active is None)
            or ("is_admin" in self.model_fields_set and self.is_admin is None)
        ):
            message = "At least one boolean security flag is required"
            raise ValueError(message)
        return self
