"""Content of one currently authorized canonical source."""

from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class SourceContent(BaseModel):
    """No storage paths or model-supplied metadata cross this boundary."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    document_id: UUID
    document_version_id: UUID
    chunk_id: UUID
    title: str
    text: str


class SourceNotFound(Exception):  # noqa: N818 -- public phase contract.
    """A source cannot be disclosed to the current principal."""
