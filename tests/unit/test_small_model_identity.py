from pathlib import Path
from typing import TYPE_CHECKING

import httpx2
import pytest

if TYPE_CHECKING:
    from pydantic import JsonValue

from rag_access_guard_api.adapters.ollama_identity import verify_model
from rag_access_guard_api.schemas.generation import GenerationUnavailable
from rag_access_guard_api.services.model_profiles import get_model_profile


@pytest.mark.anyio
@pytest.mark.parametrize(
    "profile_id", ["qwen25-instruct-small-demo-v1", "qwen25-extractive-small-demo-v1"]
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
        "system",
        "missing_system",
        "messages",
        "tokenizer",
    ],
)
async def test_small_identity_accepts_only_its_complete_verified_bundle(
    fault: str, profile_id: str
) -> None:
    profile = get_model_profile(profile_id)
    template = (
        Path(".cache/qwen3") / profile.manifest.tokenizer_revision / "template.txt"
    ).read_text(encoding="utf-8")
    info: dict[str, JsonValue] = {
        "general.architecture": "qwen3" if fault == "architecture" else "qwen2",
        "qwen2.context_length": 1024 if fault == "context" else 32768,
        "tokenizer.ggml.model": "gpt2",
        "tokenizer.ggml.pre": "wrong" if fault == "tokenizer" else "qwen2",
        "tokenizer.ggml.add_bos_token": False,
        "tokenizer.ggml.eos_token_id": 151645,
    }
    metadata: dict[str, JsonValue] = {
        "model_info": info,
        "template": "wrong" if fault == "template" else template,
        "system": "wrong"
        if fault == "system"
        else "You are Qwen, created by Alibaba Cloud. You are a helpful assistant.",
        "messages": [{"role": "user", "content": "HIDDEN"}] if fault == "messages" else [],
    }
    if fault == "missing_system":
        del metadata["system"]

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
        if fault == "none":
            await verify_model(client, profile.manifest)
        else:
            with pytest.raises(GenerationUnavailable):
                await verify_model(client, profile.manifest)
