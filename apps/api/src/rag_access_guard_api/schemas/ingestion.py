"""Immutable values at the upload and parser boundaries."""

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

type IngestionFailure = Literal[
    "unsupported_type",
    "invalid_encoding",
    "empty_text",
    "size_limit",
    "parse_failed",
    "index_failed",
]


class UploadPayload(BaseModel):
    """Actual uploaded bytes, without client-provided provenance."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    filename: str
    media_type: str
    data: bytes


class ParsedDocument(BaseModel):
    """Canonical source text, not rendered Markdown."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    text: str
    parser_revision: str
    media_type: str


class IngestionManifest(BaseModel):
    """Hashes bind the original representation and its extraction recipe."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1]
    source_sha256: str
    text_sha256: str
    byte_size: int
    parser_revision: str
    chunker_revision: str | None
    tokenizer_revision: str | None
    embedding_model_id: str | None
    embedding_model_revision: str | None
    config_sha256: str
