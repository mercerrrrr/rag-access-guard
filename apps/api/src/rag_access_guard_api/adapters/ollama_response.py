"""Discard model metadata and reject anything except a complete plain-text answer."""

from typing import ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from rag_access_guard_api.schemas.generation import GenerationUnavailable as LLMUnavailableError

MAX_ANSWER_BYTES: Final = 65536


class ReplyMessage(BaseModel):
    """Tools and media are not permitted answer channels."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True, strict=True, hide_input_in_errors=True
    )
    role: Literal["assistant"]
    content: str = Field(repr=False)
    tool_calls: tuple[()] = ()
    images: tuple[()] = ()


class Reply(BaseModel):
    """A truncated generation cannot be released as a complete answer."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True, strict=True, hide_input_in_errors=True
    )
    message: ReplyMessage = Field(repr=False)
    done: bool
    done_reason: Literal["stop"] = "stop"


def decode_answer(raw: bytes) -> str:
    """Project only final content; metadata and reasoning are discarded."""
    reply = Reply.model_validate_json(raw)
    content = reply.message.content
    if (
        not reply.done
        or not content.strip()
        or "\x00" in content
        or len(content.encode("utf-8")) > MAX_ANSWER_BYTES
    ):
        raise LLMUnavailableError
    return content
