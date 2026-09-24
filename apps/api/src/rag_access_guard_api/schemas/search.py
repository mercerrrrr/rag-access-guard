"""Bounded search requests and canonical result projections."""

from typing import ClassVar, Final

from pydantic import BaseModel, ConfigDict, Field

from rag_access_guard import SourceRef

MAX_RESULTS: Final = 5
MAX_QUERY_BYTES: Final = 16384


class SearchError(Exception):
    """Reject a search without carrying query text or database details."""


class InvalidSearchError(SearchError):
    """The query or requested result count violates the input contract."""


class InvalidProvenanceError(SearchError):
    """A canonical source failed its immutable content checks."""


class RetrievalNotConfiguredError(SearchError):
    """Calibration is missing or incompatible with the indexing recipe."""


class SearchRequest(BaseModel):
    """Only query text and a bounded result count come from the client."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    query: str = Field(min_length=1, max_length=16384, strict=True)
    limit: int = Field(default=5, ge=1, le=5, strict=True)


class SearchItem(BaseModel):
    """A result carries canonical identity, not a permanent content URL."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    source_ref: SourceRef
    text: str
    token_count: int


class SearchResponse(BaseModel):
    """Only authorized matches are represented, including the empty case."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    items: tuple[SearchItem, ...]
