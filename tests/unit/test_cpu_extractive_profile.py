import sys
from pathlib import Path
from typing import Final

import httpx2
import pytest
from pydantic import JsonValue, TypeAdapter, ValidationError
from tests.unit.test_extractive_model_profile import PASSAGE, context_for

from rag_access_guard_api.adapters.embeddings import (
    IsolatedE5EmbeddingAdapter,
    get_embedding_adapter,
)
from rag_access_guard_api.adapters.model_tokens import ModelTokenCounter
from rag_access_guard_api.adapters.ollama import OllamaAdapter
from rag_access_guard_api.config import Settings
from rag_access_guard_api.schemas.generation import GenerationUnavailable
from rag_access_guard_api.services.evaluation_identity import profile_hash
from rag_access_guard_api.services.model_profiles import get_model_profile, profile_for_manifest
from rag_access_guard_api.services.origin_context import SYNTHETIC_LABEL

PROFILE: Final = "qwen35-extractive-cpu-demo-v1"
OPTIONS: Final = {
    "num_ctx": 4096,
    "num_predict": 512,
    "temperature": 0,
    "seed": 42,
    "num_gpu": 0,
    "num_batch": 64,
}


@pytest.mark.parametrize(
    ("profile_id", "profile_digest", "counter_digest"),
    [
        (
            "qwen3-thinking-legacy-v1",
            "4f9ab877a0f305c72d0fa0f7b547c7fab29e97655553f24b6dcbfde86b73ddf6",
            "2b8c149cb130f53d0046792053bf13250876459f4ae87af1a4fdde19e7500863",
        ),
        (
            "qwen3-instruct-demo-v1",
            "46b5d47584c0a35e5c23a1aacf3fed43eb57a6e85988d6d33055394a7c700509",
            "fb4d6b13bf136f3f64e6b14c5f4ae6df673b1b5af5841aa1ee9e5e4babb0c800",
        ),
        (
            "qwen3-instruct-compact-demo-v1",
            "0160f072e0b1863ac4a212162edf78ef552cff8d127864753cc5ead1abff8fcc",
            "064df6e29223a4513ec5b917776080ec5124d1298acfae721b2fd87f1fb0c953",
        ),
        (
            "qwen25-instruct-small-demo-v1",
            "19c5bce89a419451c02f829c2929694b34c05ad22a99eb4f5e41c92aa36f017a",
            "c85a6c834a5ad0e2dea435b572194633624a76bbdca0ce5d144b765541ec575c",
        ),
        (
            "qwen25-extractive-small-demo-v1",
            "55c8ee75a435c4c69a1d08d36e97eed22a397398356674dbabd82ef6048c068d",
            "ee24ae87f1b4dd0e4bc25c7bbb365296d996a63e5f24f74316c9acaa70fce657",
        ),
        (
            "qwen35-extractive-demo-v1",
            "365be9bdbfe583cd241c5274d0119e3ccd2100f039eb8c5c053ac1c664cc664e",
            "e3c7b4807406fc9ed7f791b5ae67ac50a2e7b97b47feddcc7a8172de4cf2c645",
        ),
        (
            "qwen35-extractive-text-demo-v1",
            "ad8510343a11c8f44bfce0ce70d9a548ddc884f43c95b5aca719292e79b91548",
            "70ac1444c2a53ae898b471fa747568e572949b9abeb78f282dfa6497a91534ad",
        ),
    ],
)
def test_existing_identities_stay_pinned_when_cpu_profile_is_added(
    profile_id: str, profile_digest: str, counter_digest: str
) -> None:
    # Given the identities captured before the CPU profile addition.
    profile = get_model_profile(profile_id)
    # When resolving the old bundle, then both identities remain unchanged.
    assert profile_hash(profile) == profile_digest
    assert ModelTokenCounter(profile.manifest).identity == counter_digest


def test_cpu_profile_changes_only_runtime_options_when_reusing_text_bundle() -> None:
    # Given the installed text-only artifact and existing extraction contract.
    text = get_model_profile("qwen35-extractive-text-demo-v1")
    context = context_for(PASSAGE)
    # When choosing the independent CPU bundle.
    profile = get_model_profile(PROFILE)
    # Then manifest-type routing distinguishes the otherwise identical artifacts.
    assert type(profile.manifest) is not type(text.manifest)
    assert profile.manifest.model_dump() == text.manifest.model_dump()
    assert profile_for_manifest(profile.manifest) is profile
    assert profile_for_manifest(text.manifest) is text
    assert profile.options() == OPTIONS
    assert profile.renderer_revision == text.renderer_revision
    assert profile.runtime_version == text.runtime_version
    assert profile.template_sha256 == text.template_sha256
    assert profile.tokenizer_sha256 == text.tokenizer_sha256
    assert profile.messages(
        user_input="QUESTION", system_supplied_context=context
    ) == text.messages(user_input="QUESTION", system_supplied_context=context)
    assert profile.render(user_input="QUESTION", system_supplied_context=context) == text.render(
        user_input="QUESTION", system_supplied_context=context
    )
    assert ModelTokenCounter(profile.manifest).identity != ModelTokenCounter(text.manifest).identity
    assert profile_hash(profile) != profile_hash(text)
    assert Settings(model_profile=profile.profile_id).model_profile == PROFILE
    assert Settings().model_profile == "qwen3-thinking-legacy-v1"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model_name", "arbitrary-model"),
        ("model_digest", "0" * 64),
        ("tokenizer_id", "unknown"),
        ("tokenizer_revision", "unknown"),
        ("context_window", 2048),
        ("max_output_tokens", 513),
        ("prompt_revision", "retuned-prompt"),
        ("unknown", True),
    ],
)
def test_cpu_manifest_rejects_unpinned_operator_metadata(
    field: str, *, value: str | int | bool
) -> None:
    # Given altered metadata for the new closed manifest type.
    manifest = get_model_profile(PROFILE).manifest
    fields = manifest.model_dump()
    fields[field] = value
    # When parsing the operator bundle, then fail closed.
    with pytest.raises(ValidationError):
        _ = type(manifest).model_validate(fields)


@pytest.mark.anyio
@pytest.mark.parametrize("selection", ['{"paragraph_ids":[0]}', '{"paragraph_ids":[999]}'])
async def test_cpu_adapter_keeps_exact_selection_contract_when_runtime_is_tuned(
    monkeypatch: pytest.MonkeyPatch, selection: str
) -> None:
    # Given the server-selected CPU profile and a controlled model response.
    profile = get_model_profile(PROFILE)
    monkeypatch.setenv("RAG_ACCESS_GUARD_MODEL_PROFILE", PROFILE)
    monkeypatch.setenv(
        "RAG_ACCESS_GUARD_MODEL_TOKENIZER_PATH",
        str(Path(".cache/qwen3") / profile.manifest.tokenizer_revision / "tokenizer.json"),
    )
    context = context_for(PASSAGE)
    text = get_model_profile("qwen35-extractive-text-demo-v1")

    def respond(request: httpx2.Request) -> httpx2.Response:
        payload = TypeAdapter[JsonValue](JsonValue).validate_json(request.content)
        assert isinstance(payload, dict)
        assert payload["model"] == "rag-qwen35-text:2b-q4_k_m"
        assert payload["think"] is False
        assert payload["stream"] is False
        assert payload["options"] == OPTIONS
        assert payload["messages"] == text.messages(
            user_input="QUESTION", system_supplied_context=context
        )
        schema = payload["format"]
        assert isinstance(schema, dict)
        assert schema["additionalProperties"] is False
        assert schema["required"] == ["paragraph_ids"]
        return httpx2.Response(
            200,
            json={
                "done": True,
                "done_reason": "stop",
                "message": {"role": "assistant", "content": selection},
            },
        )

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(respond), base_url="http://127.0.0.1:11435"
    ) as client:
        # When generating, then release exact source text or reject the invalid selection.
        adapter = OllamaAdapter(client)
        if selection == '{"paragraph_ids":[0]}':
            assert (
                await adapter.generate(user_input="QUESTION", system_supplied_context=context)
                == f"{SYNTHETIC_LABEL}\n\n{PASSAGE}"
            )
        else:
            with pytest.raises(GenerationUnavailable):
                _ = await adapter.generate(user_input="QUESTION", system_supplied_context=context)


def test_cpu_profile_selects_existing_isolated_e5_on_windows64(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given the Windows64 server selection.
    monkeypatch.setenv("RAG_ACCESS_GUARD_MODEL_PROFILE", PROFILE)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "maxsize", 2**63 - 1)
    # When creating embeddings, then use the existing isolated transient lifecycle.
    adapter = get_embedding_adapter()
    assert isinstance(adapter, IsolatedE5EmbeddingAdapter)
    assert not adapter.retain_model
