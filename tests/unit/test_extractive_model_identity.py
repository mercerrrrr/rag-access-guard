from typing import TYPE_CHECKING

import httpx2
import pytest

from rag_access_guard_api.adapters.ollama_identity import verify_model
from rag_access_guard_api.schemas.generation import GenerationUnavailable
from rag_access_guard_api.services.model_profiles import get_model_profile

if TYPE_CHECKING:
    from pydantic import JsonValue


@pytest.mark.anyio
@pytest.mark.parametrize(
    "profile_id",
    [
        "qwen35-extractive-demo-v1",
        "qwen35-extractive-text-demo-v1",
        "qwen35-extractive-cpu-demo-v1",
    ],
)
@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "digest",
        "version",
        "template",
        "context",
        "architecture",
        "tokenizer",
        "system",
        "messages",
        "eos",
        "bos",
        "projector",
        "vision",
    ],
)
async def test_extractive_identity_requires_the_complete_pinned_bundle(
    fault: str, profile_id: str
) -> None:
    profile = get_model_profile(profile_id)
    metadata: dict[str, JsonValue] = {
        "model_info": {
            "general.architecture": "qwen3" if fault == "architecture" else "qwen35",
            "qwen35.context_length": 1024 if fault == "context" else 262144,
            "tokenizer.ggml.model": "gpt2",
            "tokenizer.ggml.pre": "qwen2" if fault == "tokenizer" else "qwen35",
            "tokenizer.ggml.add_bos_token": fault == "bos",
            "tokenizer.ggml.eos_token_id": 151645 if fault == "eos" else 248046,
        },
        "template": "wrong" if fault == "template" else "{{ .Prompt }}",
        "system": "OVERRIDE" if fault == "system" else "",
        "messages": [{"role": "assistant", "content": "HIDDEN"}] if fault == "messages" else [],
        "capabilities": ["completion", "tools", "thinking", "vision"]
        if fault == "vision"
        else ["completion", "tools", "thinking"],
    }
    if fault == "projector":
        metadata["projector_info"] = {"general.architecture": "clip"}

    def respond(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/version":
            return httpx2.Response(
                200, json={"version": "wrong" if fault == "version" else "0.34.4"}
            )
        if request.url.path == "/api/tags":
            return httpx2.Response(
                200,
                json={
                    "models": [
                        {
                            "name": profile.manifest.model_name,
                            "digest": "0" * 64
                            if fault == "digest"
                            else profile.manifest.model_digest,
                        }
                    ]
                },
            )
        assert request.url.path == "/api/show"
        return httpx2.Response(200, json=metadata)

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(respond), base_url="http://127.0.0.1:11435"
    ) as client:
        if fault == "none" or (
            profile_id == "qwen35-extractive-demo-v1" and fault in {"projector", "vision"}
        ):
            await verify_model(client, profile.manifest)
        else:
            with pytest.raises(GenerationUnavailable):
                await verify_model(client, profile.manifest)
