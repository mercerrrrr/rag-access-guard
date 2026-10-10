"""Immutable external corpus metadata, independent of any publisher or dataset."""

from collections import Counter
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from rag_access_guard_api.schemas.origin import DocumentOrigin

type CorpusId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")]
type CorpusText = Annotated[str, Field(min_length=1, max_length=300)]
type Sha256 = Annotated[str, Field(pattern="^[0-9a-f]{64}$")]
type CorpusMediaType = Literal[
    "text/plain",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
]


class CorpusValue(BaseModel):
    """Strict frozen values at the external manifest and report boundaries."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True, strict=True)


class CorpusDocument(CorpusValue):
    """One local upload bound to separate original-snapshot metadata."""

    key: CorpusId
    title: CorpusText
    relative_path: Annotated[str, Field(min_length=1, max_length=1024)]
    media_type: CorpusMediaType
    upload_sha256: Sha256
    origin: DocumentOrigin

    @field_validator("title")
    @classmethod
    def meaningful_title(cls, value: str) -> str:
        """Reject whitespace-only titles without rewriting supplied metadata."""
        if not value.strip():
            message = "Title must contain text"
            raise ValueError(message)
        return value

    @field_validator("relative_path")
    @classmethod
    def portable_relative_path(cls, value: str) -> str:
        """Reject Windows syntax even when validating on a POSIX host."""
        if (
            PurePosixPath(value).is_absolute()
            or PureWindowsPath(value).drive
            or "\\" in value
            or any(part in value for part in (":", "\x00"))
            or any(
                part in {"", ".", ".."} or part.endswith((" ", ".")) for part in value.split("/")
            )
        ):
            message = "Path must be a portable local relative file path"
            raise ValueError(message)
        return value


class CorpusSuggestion(CorpusValue):
    """A question with explicit document dependencies, never an access grant."""

    id: CorpusId
    question: Annotated[str, Field(min_length=1, max_length=4096)]
    required_document_keys: Annotated[tuple[CorpusId, ...], Field(min_length=1, max_length=24)]

    @model_validator(mode="after")
    def meaningful_dependencies(self) -> Self:
        """Reject empty questions and repeated dependencies."""
        if not self.question.strip() or len(set(self.required_document_keys)) != len(
            self.required_document_keys
        ):
            message = "Question and distinct dependencies are required"
            raise ValueError(message)
        return self


class DemoCorpusManifest(CorpusValue):
    """The curated 24-input profile; publisher-specific acceptance stays external."""

    schema_version: Literal[1]
    dataset_id: CorpusId
    documents: Annotated[tuple[CorpusDocument, ...], Field(min_length=24, max_length=24)]
    suggestions: Annotated[tuple[CorpusSuggestion, ...], Field(max_length=100)]

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_schema_version(cls, value: int) -> int:
        """Reject bool rather than accepting its integer equality to one."""
        if type(value) is not int:
            message = "Schema version must be an integer"
            raise ValueError(message)
        return value

    @model_validator(mode="after")
    def curated_profile(self) -> Self:
        """Bind unique identities, paths, dependency closure, and format counts."""
        keys = {document.key for document in self.documents}
        paths = {document.relative_path.casefold() for document in self.documents}
        ids = {suggestion.id for suggestion in self.suggestions}
        counts = Counter((document.origin.kind, document.media_type) for document in self.documents)
        expected = {
            ("official_public", "text/plain"): 9,
            ("official_public", "application/pdf"): 3,
            ("synthetic_demo", "text/plain"): 4,
            ("synthetic_demo", "application/pdf"): 2,
            (
                "synthetic_demo",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ): 6,
        }
        if (
            len(keys) != len(self.documents)
            or len(paths) != len(self.documents)
            or len(ids) != len(self.suggestions)
            or counts != expected
            or any(
                not set(suggestion.required_document_keys) <= keys
                for suggestion in self.suggestions
            )
        ):
            message = "Invalid curated profile, duplicate identity/path, or unknown dependency"
            raise ValueError(message)
        return self


class CorpusCounts(CorpusValue):
    """Aggregate metadata without document text or local paths."""

    documents: int
    official_public: int
    synthetic_demo: int
    txt: int
    docx: int
    pdf: int


class CorpusDocumentReport(CorpusValue):
    """Inspection evidence without original URLs, titles, paths, or raw content."""

    key: CorpusId
    kind: Literal["official_public", "synthetic_demo"]
    media_type: CorpusMediaType
    upload_sha256: Sha256
    source_sha256: Sha256
    source_check: Literal["matched_upload", "not_checked_external_original"]
    parser_revision: str
    text_sha256: Sha256
    text_characters: int
    synthetic_marker_checked: bool
    status: Literal["valid"] = "valid"


class CorpusValidationReport(CorpusValue):
    """Only successful inspection can produce an accepted report."""

    schema_version: Literal[1] = 1
    dataset_id: CorpusId
    manifest_sha256: Sha256
    status: Literal["accepted"] = "accepted"
    counts: CorpusCounts
    documents: tuple[CorpusDocumentReport, ...]
    defects: tuple[str, ...] = ()
