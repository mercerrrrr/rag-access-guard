"""Reproducible local import of verified text weights without a vision projector."""

from hashlib import sha256
from typing import ClassVar, Final

import httpx2
from pydantic import BaseModel, ConfigDict

from rag_access_guard_api.adapters.ollama_http import bounded_request
from rag_access_guard_api.adapters.ollama_identity import InstalledModels, verify_model
from rag_access_guard_api.schemas.generation import GenerationUnavailable
from rag_access_guard_api.services.model_profiles import EXTRACTIVE_PROFILE, TEXT_EXTRACTIVE_PROFILE

SOURCE_LICENSE_SHA256: Final = "50cbab8a892c5f2993b8c7351a99182507472def3b1374558308605d99b86b32"
TEXT_WEIGHTS: Final = "sha256:20cb277f0967ace47b0b5d5658e5e494a88937f7378b74b5a266496400938f4c"


class _SourceLicense(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, hide_input_in_errors=True)
    license: str


class _CreateResult(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, hide_input_in_errors=True)
    status: str


async def provision_text_model(client: httpx2.AsyncClient) -> None:
    """Create only the pinned derived tag, preserving existing models."""
    target = TEXT_EXTRACTIVE_PROFILE.manifest
    models = InstalledModels.model_validate_json(await bounded_request(client, "/api/tags"))
    matches = [model for model in models.models if model.name == target.model_name]
    if matches:
        if len(matches) != 1 or matches[0].digest != target.model_digest:
            raise GenerationUnavailable
        await verify_model(client, target)
        return
    await verify_model(client, EXTRACTIVE_PROFILE.manifest)
    source = _SourceLicense.model_validate_json(
        await bounded_request(
            client, "/api/show", payload={"model": EXTRACTIVE_PROFILE.manifest.model_name}
        )
    )
    if sha256(source.license.encode()).hexdigest() != SOURCE_LICENSE_SHA256:
        raise GenerationUnavailable
    result = _CreateResult.model_validate_json(
        await bounded_request(
            client,
            "/api/create",
            payload={
                "model": target.model_name,
                "stream": False,
                "files": {"model.gguf": TEXT_WEIGHTS},
                "renderer": "qwen3.5",
                "parser": "qwen3.5",
                "template": "{{ .Prompt }}",
                "license": source.license,
                "parameters": {
                    "presence_penalty": 1.5,
                    "temperature": 1,
                    "top_k": 20,
                    "top_p": 0.95,
                },
            },
        )
    )
    if result.status != "success":
        raise GenerationUnavailable
    await verify_model(client, target)
