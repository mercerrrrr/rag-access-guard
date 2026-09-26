"""Explicit, stateless generation port and opt-in synthetic adapter."""

import re
from dataclasses import dataclass
from typing import Protocol

from rag_access_guard_api.config import Settings


class LLMUnavailableError(Exception):
    """No usable generation result is available."""


class LLMAdapter(Protocol):
    """Keep arbitrary user text separate from verified application context."""

    async def generate(self, *, user_input: str, system_supplied_context: str) -> str:
        """Return only final plain text, without hidden conversation state."""
        ...


@dataclass(frozen=True, slots=True)
class FakeTokenCounter:
    """Exact synthetic tokenizer; not a token estimate for a real model."""

    identity: str = "synthetic-unicode-words-punctuation-v1:rag-context-v1"

    def count(self, text: str) -> int:
        """Count each Unicode word and each non-whitespace punctuation character."""
        return len(re.findall(r"\w+|[^\w\s]", text))


class FakeLLMAdapter:
    """Synthetic output with a mutable call counter, but no stored prompt or history."""

    def __init__(self) -> None:
        """Initialize this adapter's observation counter."""
        self.call_count: int = 0

    async def generate(self, *, user_input: str, system_supplied_context: str) -> str:
        """Produce a recognizable synthetic answer without interpreting instructions."""
        del user_input, system_supplied_context
        self.call_count += 1
        return "SYNTHETIC_ANSWER"


def get_llm_adapter() -> LLMAdapter:
    """Require explicit server configuration; clients cannot choose a model mode."""
    if Settings().llm_adapter != "fake":
        raise LLMUnavailableError
    return FakeLLMAdapter()
