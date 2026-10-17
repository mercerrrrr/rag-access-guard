from hashlib import sha256

import httpx2
import pytest
from pydantic import JsonValue, TypeAdapter

from rag_access_guard_api.adapters import text_model_provisioning
from rag_access_guard_api.schemas.generation import GenerationUnavailable


@pytest.mark.anyio
@pytest.mark.parametrize("fault", ["none", "conflicting_tag", "license", "final_digest"])
async def test_provisioning_creates_only_the_pinned_text_artifact(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    monkeypatch.setattr(
        text_model_provisioning, "SOURCE_LICENSE_SHA256", sha256(b"license fixture").hexdigest()
    )
    created: list[bool] = []
    parent_name = "qwen3.5:2b-q4_K_M"
    child_name = "rag-qwen35-text:2b-q4_k_m"
    parent_digest = "1e2d21a4f03ab9e4a9c1976a4b7e88f21de61d65f2c5485d8fde8942be73f1e7"
    child_digest = "84e2c930d4ab82f0e2ec10fc0f7497b59085db8304d6c2f979cbafc4009e5c1c"

    def respond(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/version":
            return httpx2.Response(200, json={"version": "0.34.4"})
        if request.url.path == "/api/tags":
            models = [{"name": parent_name, "digest": parent_digest}]
            if created or fault == "conflicting_tag":
                models.append(
                    {
                        "name": child_name,
                        "digest": "0" * 64
                        if fault in {"conflicting_tag", "final_digest"}
                        else child_digest,
                    }
                )
            return httpx2.Response(200, json={"models": models})
        if request.url.path == "/api/create":
            body = TypeAdapter[JsonValue](JsonValue).validate_json(request.content)
            assert isinstance(body, dict)
            assert body["model"] == child_name
            assert body["files"] == {
                "model.gguf": (
                    "sha256:20cb277f0967ace47b0b5d5658e5e494a88937f7378b74b5a266496400938f4c"
                )
            }
            assert "from" not in body
            assert body["renderer"] == body["parser"] == "qwen3.5"
            assert body["template"] == "{{ .Prompt }}"
            assert body["license"] == "license fixture"
            assert body["parameters"] == {
                "presence_penalty": 1.5,
                "temperature": 1,
                "top_k": 20,
                "top_p": 0.95,
            }
            created.append(True)
            return httpx2.Response(200, json={"status": "success"})
        assert request.url.path == "/api/show"
        metadata: dict[str, JsonValue] = {
            "template": "{{ .Prompt }}",
            "license": "corrupted" if fault == "license" else "license fixture",
            "capabilities": ["completion", "tools", "thinking"],
            "model_info": {
                "general.architecture": "qwen35",
                "qwen35.context_length": 262144,
                "tokenizer.ggml.model": "gpt2",
                "tokenizer.ggml.pre": "qwen35",
                "tokenizer.ggml.add_bos_token": False,
                "tokenizer.ggml.eos_token_id": 248046,
            },
        }
        return httpx2.Response(200, json=metadata)

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(respond), base_url="http://127.0.0.1:11435"
    ) as client:
        if fault == "none":
            await text_model_provisioning.provision_text_model(client)
            await text_model_provisioning.provision_text_model(client)
            assert created == [True]
        else:
            with pytest.raises(GenerationUnavailable):
                await text_model_provisioning.provision_text_model(client)
            assert created == ([True] if fault == "final_digest" else [])
