"""Version-bound publisher metadata; it never grants source access."""

from datetime import date, datetime, timedelta
from typing import Annotated, ClassVar, Final, Literal, Self, assert_never

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

SYNTHETIC_PUBLISHER: Final = "Учебный демонстрационный корпус"


class DocumentOrigin(BaseModel):
    """Honest immutable metadata; URL fields are never fetched or used as grants."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["official_public", "synthetic_demo", "user_upload"]
    publisher: Annotated[str, Field(min_length=1, max_length=200)]
    source_url: HttpUrl | None
    retrieved_at: datetime | None
    published_on: date | None
    source_sha256: Annotated[str, Field(pattern="^[0-9a-f]{64}$")]
    transformation_revision: Annotated[str, Field(min_length=1, max_length=100)]

    @field_validator("retrieved_at")
    @classmethod
    def utc_snapshot(cls, value: datetime | None) -> datetime | None:
        """Reject implicit local time or normalization that hides incorrect input."""
        if value is not None and value.utcoffset() != timedelta(0):
            message = "Snapshot time must be UTC"
            raise ValueError(message)
        return value

    @model_validator(mode="after")
    def honest_origin(self) -> Self:
        """Different origin kinds make different publisher and snapshot claims."""
        match self.kind:
            case "official_public":
                if (
                    self.source_url is None
                    or self.source_url.scheme != "https"
                    or self.retrieved_at is None
                ):
                    message = "Official origin requires an HTTPS URL and UTC snapshot time"
                    raise ValueError(message)
            case "synthetic_demo":
                if (
                    self.publisher != SYNTHETIC_PUBLISHER
                    or self.source_url is not None
                    or self.retrieved_at is not None
                ):
                    message = (
                        "Synthetic origin requires the demo publisher and no external snapshot"
                    )
                    raise ValueError(message)
            case "user_upload":
                pass
            case _:
                assert_never(self.kind)
        return self
