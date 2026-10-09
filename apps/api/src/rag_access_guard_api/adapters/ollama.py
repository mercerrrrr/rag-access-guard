"""Bounded local model transport."""

from contextlib import nullcontext
from typing import Final

import anyio
import httpx2
import psutil
from anyio.to_thread import run_sync
from pydantic import ValidationError

from rag_access_guard_api.adapters.inference_runtime import (
    InferenceRuntime,
    InferenceUnavailableError,
    current_runtime,
)
from rag_access_guard_api.adapters.model_messages import build_messages
from rag_access_guard_api.adapters.model_tokens import ModelTokenCounter, get_model_counter
from rag_access_guard_api.adapters.ollama_http import (
    TIMEOUT_SECONDS,
    RequestDispatch,
    bounded_request,
    create_client,
)
from rag_access_guard_api.adapters.ollama_identity import verify_model
from rag_access_guard_api.adapters.ollama_response import decode_answer
from rag_access_guard_api.adapters.ollama_supervisor import OllamaSupervisor
from rag_access_guard_api.config import INFERENCE_RECOVERY_SECONDS
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
        runtime: InferenceRuntime | None = None,
        supervisor: OllamaSupervisor | None = None,
    ) -> None:
        """Bind one operator configuration; callers cannot select an inference URL."""
        self._client: httpx2.AsyncClient | None = client
        self._base_url: str = base_url
        self._runtime: InferenceRuntime = runtime or current_runtime()
        self._supervisor: OllamaSupervisor | None = (
            self._runtime.own_supervisor(supervisor) if supervisor is not None else None
        )
        self.manifest: ModelManifest = manifest or ModelManifest()
        self.counter: ModelTokenCounter = (
            get_model_counter() if manifest is None else ModelTokenCounter(manifest)
        )

    async def generate(self, *, user_input: str, system_supplied_context: str) -> str:
        """Return a complete, unreleased plain-text answer."""
        if self._runtime.snapshot().generation == "unavailable":
            raise LLMUnavailableError
        containment = (
            self._supervisor.hold_containment() if self._supervisor is not None else nullcontext()
        )
        try:
            with containment:
                return await self._generate_owned(
                    user_input=user_input, system_supplied_context=system_supplied_context
                )
        except (OSError, psutil.Error, ValidationError, InferenceUnavailableError):
            self._runtime.set_generation_available(available=False)
            raise LLMUnavailableError from None

    async def _generate_owned(self, *, user_input: str, system_supplied_context: str) -> str:
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
        dispatch = RequestDispatch()
        try:
            with anyio.fail_after(TIMEOUT_SECONDS):
                claim = (
                    await run_sync(self._supervisor.begin_inference)
                    if self._supervisor is not None
                    else None
                )
                raw = await bounded_request(
                    client,
                    "/api/chat",
                    dispatch=dispatch,
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
                answer = decode_answer(raw)
                if self._supervisor is not None and claim is not None:
                    await run_sync(self._supervisor.complete_inference, claim)
                return answer
        except (
            httpx2.HTTPError,
            TimeoutError,
            ValidationError,
            UnicodeError,
            OSError,
            psutil.Error,
            InferenceUnavailableError,
        ):
            if dispatch.possibly_sent:
                await self._recover()
            elif self._supervisor is not None:
                self._runtime.set_generation_available(available=False)
            raise LLMUnavailableError from None
        except LLMUnavailableError:
            if dispatch.possibly_sent:
                await self._recover()
            raise
        except anyio.get_cancelled_exc_class():
            if dispatch.possibly_sent:
                await self._recover()
            raise

    async def _recover(self) -> None:
        self._runtime.set_generation_available(available=False)
        supervisor = self._supervisor
        if supervisor is None:
            return
        with anyio.move_on_after(INFERENCE_RECOVERY_SECONDS, shield=True) as recovery:
            try:
                await run_sync(supervisor.stop, abandon_on_cancel=True)

                def restart() -> None:
                    supervisor.start()
                    if self._runtime.closed:
                        supervisor.close_recovery_child()

                await run_sync(restart, abandon_on_cancel=True)
                while (  # noqa: ASYNC110 -- external process readiness has no local event.
                    await run_sync(supervisor.status, abandon_on_cancel=True) != "ready"
                ):
                    await anyio.sleep(0.05)
                async with create_client(self._base_url) as client:
                    await verify_model(client, self.manifest)
            except (
                OSError,
                RuntimeError,
                psutil.Error,
                ValidationError,
                InferenceUnavailableError,
                LLMUnavailableError,
            ):
                return
            if not recovery.cancel_called:
                self._runtime.set_generation_available(available=True)
