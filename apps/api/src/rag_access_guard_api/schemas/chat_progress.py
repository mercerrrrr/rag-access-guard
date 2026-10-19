"""Content-free request progress at the authenticated HTTP boundary."""

from typing import Annotated, ClassVar, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

type ProgressStage = Literal["retrieval", "generation", "authorization"]


class RequestStatus(BaseModel):
    """A metadata projection, never a source of answer or authorization state."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    request_id: UUID
    state: Literal["absent", "running", "terminal", "expired"]
    stage: ProgressStage | Literal["recovery"] | None
    elapsed_seconds: Annotated[int, Field(strict=True, ge=0)] | None

    @model_validator(mode="after")
    def consistent_progress(self) -> Self:
        """Reject live metadata attached to any nonrunning outcome."""
        match self.state:
            case "running":
                valid = self.stage is not None and self.elapsed_seconds is not None
            case "absent" | "terminal" | "expired":
                valid = self.stage is None and self.elapsed_seconds is None
        if not valid:
            message = "Inconsistent request status"
            raise ValueError(message)
        return self
