"""Canonical chunk loading shared by retrieval and policy snapshots."""

from hashlib import sha256
from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Select, select

from rag_access_guard import CandidateChunk, SourceRef
from rag_access_guard_api.adapters.tokenizer import get_tokenizer
from rag_access_guard_api.persistence import DocumentChunk, DocumentVersion
from rag_access_guard_api.schemas.search import InvalidProvenanceError
from rag_access_guard_api.services.chunking import CONTENT_LIMIT


class CanonicalChunk(BaseModel):
    """A database row retaining parent text for exact substring verification."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    document_id: UUID
    document_version_id: UUID
    chunk_id: UUID
    text: str
    content_sha256: str
    token_count: int
    char_start: int
    char_end: int
    extracted_text: str

    def candidate(self) -> CandidateChunk:
        """Bind source identity to stored bytes and the pinned content tokenizer."""
        if (
            self.char_start < 0
            or self.char_end <= self.char_start
            or self.char_end > len(self.extracted_text)
            or self.extracted_text[self.char_start : self.char_end] != self.text
            or sha256(self.text.encode("utf-8")).hexdigest() != self.content_sha256
            or get_tokenizer().count(self.text) != self.token_count
            or not 1 <= self.token_count <= CONTENT_LIMIT
        ):
            raise InvalidProvenanceError
        return CandidateChunk(
            source_ref=SourceRef(
                document_id=self.document_id,
                document_version_id=self.document_version_id,
                chunk_id=self.chunk_id,
            ),
            text=self.text,
            content_sha256=self.content_sha256,
            token_count=self.token_count,
        )


def canonical_query() -> Select[tuple[UUID, UUID, UUID, str, str, int, int, int, str]]:
    """Join the complete immutable foreign key, never a chunk ID alone."""
    return select(
        DocumentChunk.document_id,
        DocumentChunk.document_version_id,
        DocumentChunk.id.label("chunk_id"),
        DocumentChunk.text,
        DocumentChunk.content_sha256,
        DocumentChunk.token_count,
        DocumentChunk.char_start,
        DocumentChunk.char_end,
        DocumentVersion.extracted_text,
    ).join(
        DocumentVersion,
        (DocumentVersion.document_id == DocumentChunk.document_id)
        & (DocumentVersion.id == DocumentChunk.document_version_id),
    )
