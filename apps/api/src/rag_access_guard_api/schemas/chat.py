"""Chat metadata and protected turn projections, separate from stored records."""

from datetime import datetime
from typing import Annotated, ClassVar, Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from rag_access_guard_api.adapters.llm import FakeTokenCounter

MAX_INPUT_BYTES: Final = 16384
MAX_INPUT_TOKENS: Final = 1024


class CreateThread(BaseModel):
    """The session, not submitted fields, determines ownership."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class ThreadView(BaseModel):
    """Public metadata for an owned conversation."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    title: str
    revision: Annotated[int, Field(ge=0)]
    created_at: datetime


class SourceView(BaseModel):
    """A server-built link to a canonical source witness."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    document_id: UUID
    document_version_id: UUID
    chunk_id: UUID
    title: str
    url: str


class TurnIdentity(BaseModel):
    """User-owned input remains separate from protected answer content."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    id: UUID
    request_id: UUID
    user_input: str


class PendingTurn(TurnIdentity):
    """No model output is exposed before finalization."""

    state: Literal["pending"] = "pending"
    answer: None = None
    sources: tuple[()] = ()
    message: None = None


class AvailableTurn(TurnIdentity):
    """Only a read-authorized answer can populate this projection."""

    state: Literal["available"] = "available"
    answer: str
    sources: Annotated[tuple[SourceView, ...], Field(min_length=1)]
    message: None = None


class NeutralTurn(TurnIdentity):
    """A closed server message, never raw model output or exception text."""

    state: Literal["neutral"] = "neutral"
    answer: None = None
    sources: tuple[()] = ()
    message: Literal[
        "Нет доступных источников для ответа.",
        "Не удалось получить ответ. Повторите запрос.",  # noqa: RUF001
        "Права изменились во время ответа. Повторите запрос.",
        "Генерация прервана. Отправьте новый запрос.",
    ]


class UnavailableTurn(TurnIdentity):
    """Denied read projection; stored answers and provenance remain unchanged."""

    state: Literal["unavailable"] = "unavailable"
    answer: None = None
    sources: tuple[()] = ()
    message: Literal["Ответ недоступен: права на один из источников изменились."] = (
        "Ответ недоступен: права на один из источников изменились."
    )


type TurnView = Annotated[
    PendingTurn | AvailableTurn | NeutralTurn | UnavailableTurn, Field(discriminator="state")
]


class ThreadDetail(ThreadView):
    """A conversation detail with explicitly projected turns."""

    turns: tuple[TurnView, ...]


class MessageRequest(BaseModel):
    """Exact retry payload; session identity and provenance are never client fields."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    request_id: UUID
    expected_thread_revision: Annotated[int, Field(ge=0, strict=True)]
    user_input: str

    @field_validator("user_input")
    @classmethod
    def bounded_input(cls, value: str) -> str:
        """Reject oversized or non-text PostgreSQL input without truncating it."""
        if (
            not value.strip()
            or "\x00" in value
            or len(value.encode("utf-8")) > MAX_INPUT_BYTES
            or FakeTokenCounter().count(value) > MAX_INPUT_TOKENS
        ):
            message = "Invalid user input"
            raise ValueError(message)
        return value


class MessageResponse(BaseModel):
    """A committed turn, or a freshly reauthorized replay."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    thread_revision: int
    turn: TurnView
    replayed: bool
