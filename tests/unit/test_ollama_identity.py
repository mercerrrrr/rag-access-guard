import os
from pathlib import Path
from typing import TYPE_CHECKING

import httpx2
import pytest

if TYPE_CHECKING:
    from pydantic import JsonValue

from rag_access_guard_api.adapters.llm import LLMUnavailableError
from rag_access_guard_api.adapters.ollama_identity import verify_model
from rag_access_guard_api.services.model_manifest import TOKENIZER_REVISION, ModelManifest


@pytest.mark.anyio
@pytest.mark.parametrize(
    "fault",
    ["none", "digest", "version", "template", "context", "system", "missing", "unavailable"],
)
async def test_startup_requires_exact_local_model_identity(fault: str) -> None:
    manifest = ModelManifest()
    path = Path(
        os.environ.get(
            "RAG_ACCESS_GUARD_MODEL_TOKENIZER_PATH",
            f".cache/qwen3/{TOKENIZER_REVISION}/tokenizer.json",
        )
    )
    info: dict[str, JsonValue] = {
        "general.architecture": "qwen3",
        "qwen3.context_length": 262144,
        "tokenizer.ggml.model": "gpt2",
        "tokenizer.ggml.pre": "qwen2",
        "tokenizer.ggml.add_bos_token": False,
        "tokenizer.ggml.eos_token_id": 151645,
    }
    if fault == "context":
        info["qwen3.context_length"] = 1024
    metadata: dict[str, JsonValue] = {
        "model_info": info,
        "template": "different"
        if fault == "template"
        else path.with_name("template.txt").read_text(encoding="utf-8"),
        "system": "HIDDEN_SYSTEM" if fault == "system" else "",
    }

    def respond(request: httpx2.Request) -> httpx2.Response:
        if fault == "unavailable":
            message = "PRIVATE_SERVER_ERROR"
            raise httpx2.ConnectError(message)
        if request.url.path == "/api/version":
            return httpx2.Response(
                200, json={"version": "changed" if fault == "version" else "0.34.4"}
            )
        if request.url.path == "/api/tags":
            return httpx2.Response(
                200,
                json={
                    "models": []
                    if fault == "missing"
                    else [
                        {
                            "name": "qwen3:4b",
                            "digest": "0" * 64 if fault == "digest" else manifest.model_digest,
                        }
                    ]
                },
            )
        assert request.url.path == "/api/show"
        return httpx2.Response(200, json=metadata)

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(respond), base_url="http://127.0.0.1:11434"
    ) as client:
        if fault == "none":
            await verify_model(client, manifest)
        else:
            with pytest.raises(LLMUnavailableError) as raised:
                await verify_model(client, manifest)
            assert str(raised.value) == ""
