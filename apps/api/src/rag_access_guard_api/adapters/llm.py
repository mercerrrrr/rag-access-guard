"""Explicit, stateless generation port and opt-in synthetic adapter."""

import re
from dataclasses import dataclass
from typing import Protocol, assert_never

from rag_access_guard import TokenCounter
from rag_access_guard_api.adapters.inference_runtime import (
    InferenceUnavailableError,
    current_runtime,
)
from rag_access_guard_api.adapters.model_tokens import get_model_counter
from rag_access_guard_api.adapters.ollama import OllamaAdapter
from rag_access_guard_api.adapters.ollama_http import create_client
from rag_access_guard_api.adapters.ollama_identity import verify_model
from rag_access_guard_api.adapters.ollama_supervisor import OllamaSupervisor, load_supervisor_config
from rag_access_guard_api.config import Settings
from rag_access_guard_api.schemas.generation import GenerationUnavailable
from rag_access_guard_api.services.model_manifest import ModelManifest

LLMUnavailableError = GenerationUnavailable


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
    settings = Settings()
    match settings.llm_adapter:
        case "fake":
            return FakeLLMAdapter()
        case "ollama":
            supervisor: OllamaSupervisor | None = None
            if settings.ollama_supervisor_config_path is not None:
                config = load_supervisor_config(settings.ollama_supervisor_config_path)
                if config.host != settings.ollama_base_url:
                    raise InferenceUnavailableError
                supervisor = OllamaSupervisor(config)
            return OllamaAdapter(
                base_url=settings.ollama_base_url, runtime=current_runtime(), supervisor=supervisor
            )
        case "disabled":
            raise LLMUnavailableError
        case _:
            assert_never(settings.llm_adapter)


def get_token_counter() -> TokenCounter | None:
    """Never apply synthetic token counts to a real model."""
    mode = Settings().llm_adapter
    match mode:
        case "fake":
            return FakeTokenCounter()
        case "ollama":
            return get_model_counter()
        case "disabled":
            return None
        case _:
            assert_never(mode)


async def initialize_llm(settings: Settings) -> None:
    """Real generation requires a verified local tokenizer and model at startup."""
    if settings.llm_adapter == "ollama":
        if settings.ollama_supervisor_config_path is not None:
            config = load_supervisor_config(settings.ollama_supervisor_config_path)
            supervisor = current_runtime().own_supervisor(OllamaSupervisor(config))
            if config.host != settings.ollama_base_url or supervisor.status() != "ready":
                raise InferenceUnavailableError
        _ = get_model_counter()
        async with create_client(settings.ollama_base_url) as client:
            await verify_model(client, ModelManifest())
