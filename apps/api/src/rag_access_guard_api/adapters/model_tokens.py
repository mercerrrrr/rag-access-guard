"""Offline, artifact-verified token accounting for the local generation model."""

import os
from dataclasses import dataclass, field
from functools import cache
from hashlib import sha256
from pathlib import Path

from tokenizers import Tokenizer

from rag_access_guard_api.schemas.generation import GenerationUnavailable as LLMUnavailableError
from rag_access_guard_api.services.model_profiles import (
    GenerationManifest,
    ModelProfile,
    get_model_profile,
    profile_for_manifest,
)


@dataclass(frozen=True, slots=True)
class ModelTokenCounter:
    """Count complete requests without truncation, using a verified native tokenizer."""

    manifest: GenerationManifest
    tokenizer_path: Path | None = None
    _tokenizer: Tokenizer = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Bind counting to checked local bytes before any model call."""
        manifest = self.manifest
        path = self.tokenizer_path or Path(
            os.environ.get(
                "RAG_ACCESS_GUARD_MODEL_TOKENIZER_PATH",
                f".cache/qwen3/{manifest.tokenizer_revision}/tokenizer.json",
            )
        )
        try:
            raw = path.read_bytes()
            if sha256(raw).hexdigest() != profile_for_manifest(manifest).tokenizer_sha256:
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
        profile = profile_for_manifest(self.manifest)
        payload = (
            f"{profile.profile_id}:{profile.tokenizer_sha256}:{profile.template_sha256}:"
            f"{profile.runtime_version}:{profile.renderer_revision}:5000:1024:"
            f"{profile.options()}:{self.manifest.model_dump_json()}"
        )
        return sha256(payload.encode()).hexdigest()

    def count(self, text: str) -> int:
        """Count exact content tokens without padding or truncation."""
        return len(self._tokenizer.encode(text, add_special_tokens=False).ids)

    def count_request(self, *, user_input: str, system_supplied_context: str) -> int:
        """Include the actual chat template and generation prefix."""
        return self.count(
            profile_for_manifest(self.manifest).render(
                user_input=user_input, system_supplied_context=system_supplied_context
            )
        )


def get_model_counter(profile_id: str | None = None) -> ModelTokenCounter:
    """Reuse only verified, immutable tokenizer state within the process."""
    profile = get_model_profile(
        profile_id or os.environ.get("RAG_ACCESS_GUARD_MODEL_PROFILE", "qwen3-thinking-legacy-v1")
    )
    path = os.environ.get(
        "RAG_ACCESS_GUARD_MODEL_TOKENIZER_PATH",
        f".cache/qwen3/{profile.manifest.tokenizer_revision}/tokenizer.json",
    )
    return _profile_counter(profile, path)


@cache
def _profile_counter(profile: ModelProfile, path: str) -> ModelTokenCounter:
    return ModelTokenCounter(profile.manifest, Path(path))
