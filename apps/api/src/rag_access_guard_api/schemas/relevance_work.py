"""Private nonce-bound messages for one contained relevance stage."""

from pathlib import Path
from typing import Annotated, ClassVar, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from rag_access_guard_api.schemas.relevance_config import INPUT_BYTE_LIMIT, PASSAGE_LIMIT


class RelevanceWorkRequest(BaseModel):
    """Carry only the already authorized ordered texts to the fixed local worker."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True, hide_input_in_errors=True
    )
    kind: Literal["relevance"] = "relevance"
    nonce: UUID
    config_path: Path = Field(repr=False)
    question: str = Field(min_length=1, repr=False)
    texts: Annotated[tuple[str, ...], Field(min_length=1, max_length=PASSAGE_LIMIT, repr=False)]

    @model_validator(mode="after")
    def bounded_absolute_input(self) -> Self:
        """Reject invalid paths and full oversized bodies before native allocation."""
        if (
            not self.config_path.is_absolute()
            or any(not text.strip() for text in self.texts)
            or len(self.question.encode()) + sum(len(text.encode()) for text in self.texts)
            > INPUT_BYTE_LIMIT
        ):
            message = "Invalid bounded relevance work input"
            raise ValueError(message)
        return self


class RelevanceWorkResponse(BaseModel):
    """Reject nonfinite, textual or unbounded results before choosing a generation mode."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True, hide_input_in_errors=True, allow_inf_nan=False
    )
    kind: Literal["relevance"] = "relevance"
    nonce: UUID
    scores: Annotated[tuple[float, ...], Field(min_length=1, max_length=PASSAGE_LIMIT, repr=False)]
