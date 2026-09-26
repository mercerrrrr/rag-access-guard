"""Offline, artifact-verified token accounting for the local generation model."""

import os
from dataclasses import dataclass, field
from functools import cache
from hashlib import sha256
from pathlib import Path

from tokenizers import Tokenizer

from rag_access_guard_api.adapters.model_messages import render_request
from rag_access_guard_api.schemas.generation import GenerationUnavailable as LLMUnavailableError
from rag_access_guard_api.services.model_manifest import TOKENIZER_SHA256, ModelManifest


@dataclass(frozen=True, slots=True)
class ModelTokenCounter:
    """Count complete requests without truncation, using a verified native tokenizer."""

    manifest: ModelManifest
    _tokenizer: Tokenizer = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Bind counting to checked local bytes before any model call."""
        manifest = self.manifest
        path = Path(
            os.environ.get(
                "RAG_ACCESS_GUARD_MODEL_TOKENIZER_PATH",
                f".cache/qwen3/{manifest.tokenizer_revision}/tokenizer.json",
            )
        )
        try:
            raw = path.read_bytes()
            if sha256(raw).hexdigest() != TOKENIZER_SHA256:
                raise LLMUnavailableError
            tokenizer = Tokenizer.from_str(raw.decode("utf-8"))
        except OSError:
            raise LLMUnavailableError from None
        tokenizer.no_padding()
        tokenizer.no_truncation()
        object.__setattr__(self, "_tokenizer", tokenizer)

    @property
    def identity(self) -> str:
        """Include the model, renderer revision and budgets in context integrity."""
        return sha256(self.manifest.model_dump_json().encode()).hexdigest()

    def count(self, text: str) -> int:
        """Count exact content tokens without padding or truncation."""
        return len(self._tokenizer.encode(text, add_special_tokens=False).ids)

    def count_request(self, *, user_input: str, system_supplied_context: str) -> int:
        """Include the actual chat template and generation prefix."""
        return self.count(
            render_request(user_input=user_input, system_supplied_context=system_supplied_context)
        )


@cache
def get_model_counter() -> ModelTokenCounter:
    """Reuse only verified, immutable tokenizer state within the process."""
    return ModelTokenCounter(ModelManifest())
