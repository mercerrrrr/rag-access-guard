"""Bounded local model transport."""

from typing import Final

import anyio
import httpx2
from pydantic import ValidationError

from rag_access_guard_api.adapters.model_messages import build_messages
from rag_access_guard_api.adapters.model_tokens import ModelTokenCounter, get_model_counter
from rag_access_guard_api.adapters.ollama_http import (
    TIMEOUT_SECONDS,
    bounded_request,
    create_client,
)
from rag_access_guard_api.adapters.ollama_identity import verify_model
from rag_access_guard_api.adapters.ollama_response import decode_answer
from rag_access_guard_api.schemas.generation import GenerationUnavailable as LLMUnavailableError
from rag_access_guard_api.services.model_manifest import ModelManifest

MAX_INPUT_BYTES: Final = 16384
MAX_INPUT_TOKENS: Final = 1024
MAX_CONTEXT_TOKENS: Final = 5000


class OllamaAdapter:
    """Generate from an explicit request without retaining conversation state."""

    def __init__(
        self,
        client: httpx2.AsyncClient | None = None,
        *,
        base_url: str = "http://127.0.0.1:11434",
        manifest: ModelManifest | None = None,
    ) -> None:
        """Bind one operator configuration; callers cannot select an inference URL."""
        self._client: httpx2.AsyncClient | None = client
        self._base_url: str = base_url
        self.manifest: ModelManifest = manifest or ModelManifest()
        self.counter: ModelTokenCounter = (
            get_model_counter() if manifest is None else ModelTokenCounter(manifest)
        )

    async def generate(self, *, user_input: str, system_supplied_context: str) -> str:
        """Return a complete, unreleased plain-text answer."""
        if self._client is None:
            try:
                with anyio.fail_after(TIMEOUT_SECONDS):
                    async with create_client(self._base_url) as client:
                        await verify_model(client, self.manifest)
                        return await self._generate(
                            client,
                            user_input=user_input,
                            system_supplied_context=system_supplied_context,
                        )
            except TimeoutError:
                raise LLMUnavailableError from None
        return await self._generate(
            self._client, user_input=user_input, system_supplied_context=system_supplied_context
        )

    async def _generate(
        self, client: httpx2.AsyncClient, *, user_input: str, system_supplied_context: str
    ) -> str:
        if (
            len(user_input.encode("utf-8")) > MAX_INPUT_BYTES
            or self.counter.count(user_input) > MAX_INPUT_TOKENS
            or self.counter.count(system_supplied_context) > MAX_CONTEXT_TOKENS
            or self.counter.count_request(
                user_input=user_input, system_supplied_context=system_supplied_context
            )
            + self.manifest.max_output_tokens
            > self.manifest.context_window
        ):
            raise LLMUnavailableError
        try:
            with anyio.fail_after(TIMEOUT_SECONDS):
                raw = await bounded_request(
                    client,
                    "/api/chat",
                    payload={
                        "model": self.manifest.model_name,
                        "stream": False,
                        "messages": [
                            {"role": message["role"], "content": message["content"]}
                            for message in build_messages(
                                user_input=user_input,
                                system_supplied_context=system_supplied_context,
                            )
                        ],
                        "options": {
                            "num_ctx": self.manifest.context_window,
                            "num_predict": self.manifest.max_output_tokens,
                        },
                    },
                )
                return decode_answer(raw)
        except (httpx2.HTTPError, TimeoutError, ValidationError, UnicodeError):
            raise LLMUnavailableError from None
