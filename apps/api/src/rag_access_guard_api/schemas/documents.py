"""Administrative metadata contracts without document content."""

from datetime import datetime
from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StrictBool


class DocumentSummary(BaseModel):
    """Logical document metadata."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    title: str
    is_active: bool
    active_version_id: UUID | None
    created_at: datetime


class DocumentVersionSummary(BaseModel):
    """Version metadata; source bytes and text remain private."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    document_id: UUID
    status: str
    created_at: datetime
    content_sha256: str
    byte_size: int


class DocumentList(BaseModel):
    """Ordered administrative document listing."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    items: list[DocumentSummary]


class DocumentVersionList(BaseModel):
    """Ordered administrative version listing."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    items: list[DocumentVersionSummary]


class DocumentPatch(BaseModel):
    """Only mutable logical metadata can be changed."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    title: str | None = None
    is_active: StrictBool | None = None
