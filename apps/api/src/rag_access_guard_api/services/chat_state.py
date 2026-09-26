"""Internal persisted-turn projection and immutable generation binding."""

import json
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import ClassVar, Literal, assert_never
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from rag_access_guard import PreparedContext
from rag_access_guard_api.schemas.chat import MessageRequest, NeutralTurn

type NeutralReason = Literal[
    "no_context", "generation_unavailable", "policy_changed", "interrupted"
]
type ConflictReason = Literal["thread_conflict", "request_conflict", "request_in_progress"]


@dataclass(frozen=True, slots=True)
class ChatConflict:
    """Returned after the transaction so lazy expiry is not rolled back by HTTP 409."""

    reason: ConflictReason


class StoredTurn(BaseModel):
    """Metadata loaded before authorization, deliberately excluding the answer."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)
    id: UUID
    thread_id: UUID
    request_id: UUID
    request_sha256: bytes
    expected_thread_revision: int
    user_input: str
    state: Literal["pending", "available", "neutral"]
    provenance_complete: bool
    neutral_reason: NeutralReason | None
    lease_expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class Reservation:
    """Server-bound request identity carried across unlocked model inference."""

    principal_id: UUID
    session_id: UUID
    thread_id: UUID
    request_id: UUID
    thread_revision: int


@dataclass(frozen=True, slots=True)
class GenerationAttempt(Reservation):
    """The exact prepared context belongs to one session, thread and reservation."""

    prepared: PreparedContext


def request_hash(request: MessageRequest) -> bytes:
    """Hash exact input and original revision with an unambiguous versioned encoding."""
    return sha256(
        json.dumps(
            ["chat-request-v1", request.user_input, request.expected_thread_revision],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).digest()


def neutral_view(turn: StoredTurn, reason: NeutralReason) -> NeutralTurn:
    """Choose only a closed server message, never model or exception text."""
    match reason:
        case "no_context":
            message = "Нет доступных источников для ответа."
        case "generation_unavailable":
            message = "Не удалось получить ответ. Повторите запрос."  # noqa: RUF001
        case "policy_changed":
            message = "Права изменились во время ответа. Повторите запрос."
        case "interrupted":
            message = "Генерация прервана. Отправьте новый запрос."
        case _:
            assert_never(reason)
    return NeutralTurn(
        id=turn.id, request_id=turn.request_id, user_input=turn.user_input, message=message
    )
