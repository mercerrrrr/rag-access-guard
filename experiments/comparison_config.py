"""Controlled experiment inputs independent of orchestration and host implementation."""

import json
from hashlib import sha256
from typing import Annotated

from pydantic import Field

from experiments.scenario_types import FrozenModel
from experiments.seed import SYNTHETIC_RECIPE
from rag_access_guard.context import RENDERER_REVISION
from rag_access_guard_api.adapters.embeddings import MODEL_ID
from rag_access_guard_api.adapters.llm import FakeTokenCounter
from rag_access_guard_api.adapters.model_tokens import ModelTokenCounter
from rag_access_guard_api.adapters.tokenizer import MODEL_REVISION, TOKENIZER_IDENTITY
from rag_access_guard_api.config import Settings
from rag_access_guard_api.schemas.retrieval_config import load_retrieval_config
from rag_access_guard_api.services.chunking import CHUNKER_REVISION
from rag_access_guard_api.services.model_manifest import ModelManifest


class ComparisonConfig(FrozenModel):
    """Use the same verified renderer, synthetic model and budget in both arms."""

    seed: int = 63
    max_context_tokens: Annotated[int, Field(ge=0, le=5000)] = 5000
    max_prior_turns: Annotated[int, Field(ge=0, le=4)] = 4
    top_k: Annotated[int, Field(ge=1, le=5)] = 5
    renderer_identity: str = RENDERER_REVISION
    tokenizer_identity: str = FakeTokenCounter().identity
    model_identity: str = "synthetic-context-markers-v1"
    model_manifest: ModelManifest | None = None

    def token_counter(self) -> FakeTokenCounter | ModelTokenCounter:
        """Use the same verified tokenizer in both arms, without synthetic fallback."""
        return (
            FakeTokenCounter()
            if self.model_manifest is None
            else ModelTokenCounter(self.model_manifest)
        )

    def validate_runtime(self) -> None:
        """Reject claimed identities that do not match the adapters actually executed."""
        expected_tokenizer = (
            FakeTokenCounter().identity
            if self.model_manifest is None
            else sha256(self.model_manifest.model_dump_json().encode()).hexdigest()
        )
        expected_model = (
            "synthetic-context-markers-v1"
            if self.model_manifest is None
            else f"{self.model_manifest.model_name}@{self.model_manifest.model_digest}"
        )
        if (
            self.renderer_identity != RENDERER_REVISION
            or self.tokenizer_identity != expected_tokenizer
            or self.model_identity != expected_model
        ):
            message = "Experiment adapter identity mismatch"
            raise ValueError(message)

    def digest(self) -> str:
        """Hash all controlled values without credentials or local environment paths."""
        return sha256(self.model_dump_json().encode()).hexdigest()

    def runtime_digest(self, *, expected: str | None = None) -> str:
        """Bind comparison settings to the actual retrieval recipe and synthetic adapters."""
        self.validate_runtime()
        retrieval = load_retrieval_config(Settings().retrieval_config_path)
        identities = (
            self.digest(),
            retrieval.config_sha256,
            TOKENIZER_IDENTITY,
            CHUNKER_REVISION,
            f"{SYNTHETIC_RECIPE}:{MODEL_ID}:{MODEL_REVISION}",
        )
        digest = sha256(json.dumps(identities, separators=(",", ":")).encode()).hexdigest()
        if expected is not None and expected != digest:
            message = "Experiment runtime configuration changed during arm"
            raise ValueError(message)
        return digest
