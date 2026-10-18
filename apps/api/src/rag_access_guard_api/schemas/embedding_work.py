"""Private bounded messages between the API and its embedding worker."""

from pathlib import Path
from typing import Annotated, ClassVar, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EmbeddingWorkRequest(BaseModel):
    """Bind one ordered batch to a local model and response nonce."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True, hide_input_in_errors=True
    )
    nonce: UUID
    path: Path = Field(repr=False)
    texts: Annotated[tuple[str, ...], Field(min_length=1, max_length=16, repr=False)]
    kind: Literal["query", "passage"]

    @model_validator(mode="after")
    def absolute_nonempty(self) -> Self:
        """Reject cwd-dependent artifacts and empty inputs before loading weights."""
        if not self.path.is_absolute() or any(not text.strip() for text in self.texts):
            raise ValueError
        return self


class EmbeddingWorkResponse(BaseModel):
    """Carry only computed vectors; shape and norm are checked by the caller."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True, hide_input_in_errors=True, allow_inf_nan=False
    )
    nonce: UUID
    vectors: Annotated[
        tuple[tuple[float, ...], ...], Field(min_length=1, max_length=16, repr=False)
    ]
