"""Strict parsing of Guard's existing canonical context at the model boundary."""

from typing import Annotated, ClassVar, Literal, Self
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, TypeAdapter, model_validator

from rag_access_guard_api.schemas.origin_labels import OriginLabel

type EvidenceRef = tuple[UUID, UUID, UUID]
type EvidenceChunk = tuple[UUID, UUID, UUID, Annotated[str, Field(min_length=1)]]
type OriginRow = tuple[
    UUID, UUID, UUID, Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")], OriginLabel
]


class EvidenceValue(BaseModel):
    """Parse external JSON strictly and exclude document bodies from error messages."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True, hide_input_in_errors=True
    )


class EvidencePriorTurn(EvidenceValue):
    """User questions may guide selection but are never quote candidates."""

    turn_id: UUID
    user_input: str = Field(repr=False)
    answer: str = Field(min_length=1, repr=False)
    source_refs: tuple[EvidenceRef, ...] = Field(min_length=1)


def _context_version(value: object) -> int:
    match value:
        case int() as version if type(version) is int:
            return version
        case _:
            message = "Context version must be an integer"
            raise ValueError(message)


class SingleTurnEvidence(EvidenceValue):
    """The unchanged version-one Guard serialization."""

    version: Annotated[Literal[1], BeforeValidator(_context_version)]
    chunks: tuple[EvidenceChunk, ...] = Field(repr=False)


class HistoricalEvidence(EvidenceValue):
    """The unchanged version-two Guard serialization."""

    version: Annotated[Literal[2], BeforeValidator(_context_version)]
    chunks: tuple[EvidenceChunk, ...] = Field(repr=False)
    history: tuple[EvidencePriorTurn, ...] = Field(repr=False)


class EvidenceSelection(EvidenceValue):
    """Only ordinal selection is model output; no model-written answer is accepted."""

    paragraph_ids: tuple[Annotated[int, Field(ge=0)], ...] = Field(max_length=3)


class SufficientEvidenceSelection(EvidenceSelection):
    """Require a consistent explicit decision before rendering any quotation."""

    decision: Literal["complete", "insufficient"]

    @model_validator(mode="after")
    def consistent_decision(self) -> Self:
        """Complete answers select evidence; insufficient answers cannot select it."""
        if bool(self.paragraph_ids) != (self.decision == "complete"):
            raise ValueError
        return self


type CanonicalEvidence = SingleTurnEvidence | HistoricalEvidence
CONTEXT = TypeAdapter[CanonicalEvidence](CanonicalEvidence)
ORIGINS = TypeAdapter[tuple[OriginRow, ...]](tuple[OriginRow, ...])
