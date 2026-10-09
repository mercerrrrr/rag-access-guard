"""Bounded metadata and artifact probes; physical quarantine is never reopened here."""

import os
from functools import partial
from pathlib import Path
from time import monotonic
from typing import Final, Literal, assert_never

import anyio
import psutil
from anyio.to_thread import run_sync
from pydantic import ValidationError

from rag_access_guard_api.adapters.e5_artifacts import verify_e5_artifacts
from rag_access_guard_api.adapters.inference_runtime import current_runtime
from rag_access_guard_api.adapters.model_tokens import get_model_counter
from rag_access_guard_api.adapters.ollama_http import create_client
from rag_access_guard_api.adapters.ollama_identity import verify_model
from rag_access_guard_api.adapters.ollama_supervisor import OllamaSupervisor, load_supervisor_config
from rag_access_guard_api.adapters.tokenizer import MODEL_REVISION, TokenizerUnavailableError
from rag_access_guard_api.config import Settings
from rag_access_guard_api.schemas.embedding_vectors import EmbeddingError
from rag_access_guard_api.schemas.generation import GenerationUnavailable, InferenceUnavailableError
from rag_access_guard_api.schemas.retrieval_config import load_retrieval_config
from rag_access_guard_api.schemas.search import RetrievalNotConfiguredError
from rag_access_guard_api.schemas.system import ModelStatus, SearchStatus, SystemStatus
from rag_access_guard_api.services.model_profiles import get_model_profile

PROBE_SECONDS: Final = 5
READINESS_TTL_SECONDS: Final = 10


class SystemReadiness:
    """Cache checked metadata only; every caller still authenticates its current session."""

    def __init__(self, settings: Settings) -> None:
        """Bind one server configuration for the application lifetime."""
        self._settings: Settings = settings
        self._lock: anyio.Lock = anyio.Lock()
        self._checked_at: float = float("-inf")
        self._model_ready: bool = False
        self._accepted_busy_ready: bool = False
        self._search_ready: bool = False
        self._supervisor: OllamaSupervisor | None = None
        if settings.llm_adapter == "ollama" and settings.ollama_supervisor_config_path is not None:
            config = load_supervisor_config(settings.ollama_supervisor_config_path)
            if config.host != settings.ollama_base_url:
                message = "Supervisor host differs from configured model endpoint"
                raise ValueError(message)
            self._supervisor = OllamaSupervisor(config)

    async def snapshot(self) -> SystemStatus:
        """A fresh physical unavailable state overrides any successful metadata TTL."""
        try:
            with anyio.fail_after(PROBE_SECONDS):
                async with self._lock:
                    if monotonic() - self._checked_at >= READINESS_TTL_SECONDS:
                        self._checked_at = monotonic()
                        self._model_ready = False
                        self._accepted_busy_ready = False
                        self._search_ready = False
                        self._model_ready = await self._probe_model()
                        self._search_ready = await self._probe_search()
                        if self._settings.llm_adapter == "ollama":
                            current_runtime().set_model_available(available=self._model_ready)
        except TimeoutError:
            if self._settings.llm_adapter == "ollama":
                current_runtime().set_model_available(available=False)
            return self._project(
                model_ready=(
                    self._accepted_busy_ready and current_runtime().snapshot().generation == "busy"
                ),
                search_ready=False,
            )
        physical = current_runtime().snapshot()
        return self._project(
            model_ready=(
                self._model_ready or (self._accepted_busy_ready and physical.generation == "busy")
            )
            and physical.generation != "unavailable",
            search_ready=self._search_ready and physical.embeddings != "unavailable",
        )

    async def _probe_search(self) -> bool:
        def verify() -> None:
            _ = load_retrieval_config(self._settings.retrieval_config_path)
            path = Path(
                os.environ.get("RAG_ACCESS_GUARD_MODEL_PATH", f".cache/e5/{MODEL_REVISION}")
            )
            _ = verify_e5_artifacts(path)

        try:
            await run_sync(verify, abandon_on_cancel=True)
        except (OSError, RuntimeError, ValueError, EmbeddingError, RetrievalNotConfiguredError):
            return False
        return True

    async def _probe_model(self) -> bool:
        if self._settings.llm_adapter != "ollama":
            return False
        if current_runtime().generation_quarantined:
            return False
        try:
            if self._supervisor is not None:
                supervisor = current_runtime().own_supervisor(self._supervisor)
                active = current_runtime().generation_busy and await run_sync(
                    supervisor.has_live_inference_claim, abandon_on_cancel=True
                )
                if (
                    not active
                    and await run_sync(supervisor.status, abandon_on_cancel=True) != "ready"
                    and (
                        current_runtime().generation_quarantined
                        or not current_runtime().generation_busy
                        or not await run_sync(
                            supervisor.has_live_inference_claim, abandon_on_cancel=True
                        )
                    )
                ):
                    return False
            self._accepted_busy_ready = current_runtime().generation_busy
            _ = await run_sync(
                partial(get_model_counter, self._settings.model_profile), abandon_on_cancel=True
            )
            async with create_client(self._settings.ollama_base_url) as client:
                await verify_model(client, get_model_profile(self._settings.model_profile).manifest)
        except (
            GenerationUnavailable,
            InferenceUnavailableError,
            OSError,
            psutil.Error,
            ValidationError,
            TokenizerUnavailableError,
        ):
            return False
        return True

    def _project(self, *, model_ready: bool, search_ready: bool) -> SystemStatus:
        state: Literal["ready", "unavailable", "disabled", "test"]
        match self._settings.llm_adapter:
            case "disabled":
                mode = "disabled"
                name = None
                state = "disabled"
            case "fake":
                mode = "test"
                name = None
                state = "test"
            case "ollama":
                mode = "local"
                name = get_model_profile(self._settings.model_profile).manifest.model_name
                state = "ready" if model_ready else "unavailable"
            case _:
                assert_never(self._settings.llm_adapter)
        return SystemStatus(
            mode=mode,
            model=ModelStatus(name=name, state=state),
            search=SearchStatus(state="ready" if search_ready else "unavailable"),
        )
