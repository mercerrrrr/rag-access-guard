"""Content of one currently authorized canonical source."""

from dataclasses import dataclass, field
from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from rag_access_guard_api.schemas.origin import DocumentOrigin


class SourceContent(BaseModel):
    """No storage paths or model-supplied metadata cross this boundary."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    document_id: UUID
    document_version_id: UUID
    chunk_id: UUID
    title: str
    text: str
    origin: DocumentOrigin | None = None


class SourceNotFound(Exception):  # noqa: N818 -- public phase contract.
    """A source cannot be disclosed to the current principal."""


@dataclass(frozen=True, slots=True)
class OriginalContent:
    """Bounded immutable bytes, never a storage URL or client filename."""

    data: bytes = field(repr=False)
    media_type: str
    filename: str
