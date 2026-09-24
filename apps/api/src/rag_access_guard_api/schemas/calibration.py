"""Private calibration data and independent evaluation split identities."""

from hashlib import sha256
from typing import ClassVar, Final, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from rag_access_guard_api.adapters.tokenizer import get_tokenizer
from rag_access_guard_api.schemas.search import InvalidSearchError
from rag_access_guard_api.services.chunking import CONTENT_LIMIT, EMBEDDING_LIMIT

QUERIES_PER_LANGUAGE: Final = 6
EMPTY_QUERIES_PER_LANGUAGE: Final = 2


class CorpusChunk(BaseModel):
    """Synthetic canonical content with an explicit stable chunk identity."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    text: str = Field(min_length=1)


class CalibrationQuery(BaseModel):
    """Gold relevance is supplied independently of embedding similarity."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1)
    language: Literal["ru", "en"]
    query: str = Field(min_length=1)
    relevant_chunk_ids: tuple[UUID, ...]


class EvaluationManifest(BaseModel):
    """Only identities and hashes are needed to reject split overlap."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    query_ids: tuple[str, ...]
    corpus_hashes: tuple[str, ...]


class CalibrationDataset(BaseModel):
    """The initial balanced twelve-query calibration contract."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1]
    split: Literal["calibration"]
    corpus: tuple[CorpusChunk, ...]
    queries: tuple[CalibrationQuery, ...]

    @model_validator(mode="after")
    def require_valid_gold(self) -> Self:
        """Reject ambiguous IDs, absent gold, oversized content and unbalanced splits."""
        ids = {chunk.id for chunk in self.corpus}
        tokenizer = get_tokenizer()
        valid = (
            bool(ids)
            and len(ids) == len(self.corpus)
            and len({query.id for query in self.queries}) == len(self.queries)
            and all(
                chunk.text.strip() and 1 <= tokenizer.count(chunk.text) <= CONTENT_LIMIT
                for chunk in self.corpus
            )
            and all(
                query.query.strip()
                and tokenizer.embedding_input_tokens(query.query, kind="query") <= EMBEDDING_LIMIT
                and set(query.relevant_chunk_ids) <= ids
                and len(set(query.relevant_chunk_ids)) == len(query.relevant_chunk_ids)
                for query in self.queries
            )
            and all(
                sum(query.language == language for query in self.queries) == QUERIES_PER_LANGUAGE
                and sum(
                    query.language == language and not query.relevant_chunk_ids
                    for query in self.queries
                )
                == EMPTY_QUERIES_PER_LANGUAGE
                for language in ("ru", "en")
            )
        )
        if not valid:
            raise InvalidSearchError
        return self

    def require_disjoint(self, evaluation: EvaluationManifest) -> None:
        """Prevent calibration reuse of frozen evaluation identities or content."""
        if {query.id for query in self.queries} & set(evaluation.query_ids) or {
            sha256(chunk.text.encode("utf-8")).hexdigest() for chunk in self.corpus
        } & set(evaluation.corpus_hashes):
            raise InvalidSearchError
