"""Verify the local runtime and full model identity before serving generation."""

from hashlib import sha256
from typing import ClassVar, Literal, assert_never

import anyio
import httpx2
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from rag_access_guard_api.adapters.ollama_http import bounded_request
from rag_access_guard_api.adapters.qwen25_identity import SmallModelMetadata
from rag_access_guard_api.adapters.qwen35_identity import ExtractiveModelMetadata
from rag_access_guard_api.schemas.generation import GenerationUnavailable as LLMUnavailableError
from rag_access_guard_api.services.extractive_manifest import ExtractiveManifest
from rag_access_guard_api.services.model_manifest import ModelManifest
from rag_access_guard_api.services.model_profiles import (
    GenerationManifest,
    InstructManifest,
    profile_for_manifest,
)
from rag_access_guard_api.services.small_model_manifest import SmallInstructManifest


class RuntimeVersion(BaseModel):
    """Parse only the version used to audit server-side rendering."""

    version: str


class InstalledModel(BaseModel):
    """A model name alone is not a stable identity."""

    name: str
    digest: str


class InstalledModels(BaseModel):
    """The local model registry, never the public remote registry."""

    models: tuple[InstalledModel, ...]


class ModelInfo(BaseModel):
    """Match the tokenizer family and architecture of the audited GGUF."""

    architecture: Literal["qwen3"] = Field(alias="general.architecture")
    context_length: Literal[262144] = Field(alias="qwen3.context_length")
    tokenizer: Literal["gpt2"] = Field(alias="tokenizer.ggml.model")
    pretokenizer: Literal["qwen2"] = Field(alias="tokenizer.ggml.pre")
    add_bos: Literal[False] = Field(alias="tokenizer.ggml.add_bos_token")
    eos: Literal[151645] = Field(alias="tokenizer.ggml.eos_token_id")


class ModelMetadata(BaseModel):
    """Reject embedded history or system overrides from an unexpected model."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, hide_input_in_errors=True)
    template: str = Field(repr=False)
    model_info: ModelInfo
    system: Literal[""] = ""
    messages: tuple[()] = ()


async def verify_model(client: httpx2.AsyncClient, manifest: GenerationManifest) -> None:
    """Fail closed on unavailable metadata, moving tags or changed serialization."""
    profile = profile_for_manifest(manifest)
    try:
        with anyio.fail_after(15):
            version = RuntimeVersion.model_validate_json(
                await bounded_request(client, "/api/version")
            )
            models = InstalledModels.model_validate_json(await bounded_request(client, "/api/tags"))
            raw = await bounded_request(client, "/api/show", payload={"model": manifest.model_name})
            match manifest:
                case ExtractiveManifest():
                    metadata = ExtractiveModelMetadata.model_validate_json(raw)
                    valid_system = True
                case SmallInstructManifest():
                    metadata = SmallModelMetadata.model_validate_json(raw)
                    valid_system = (
                        sha256(metadata.system.encode()).hexdigest() == manifest.model_system_sha256
                    )
                case ModelManifest() | InstructManifest():
                    metadata = ModelMetadata.model_validate_json(raw)
                    valid_system = True
                case _:
                    assert_never(manifest)
            matches = [m for m in models.models if m.name == manifest.model_name]
            if (
                version.version != profile.runtime_version
                or not valid_system
                or len(matches) != 1
                or matches[0].digest != manifest.model_digest
                or sha256(metadata.template.encode()).hexdigest() != profile.template_sha256
                or manifest.context_window > metadata.model_info.context_length
            ):
                raise LLMUnavailableError
    except (httpx2.HTTPError, TimeoutError, ValidationError):
        raise LLMUnavailableError from None
