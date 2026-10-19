import sys
from pathlib import Path
from typing import Final

import httpx2
import pytest
from pydantic import JsonValue, TypeAdapter, ValidationError
from tests.unit.test_extractive_model_profile import PASSAGE, context_for

from rag_access_guard_api.adapters import embeddings
from rag_access_guard_api.adapters.embeddings import E5EmbeddingAdapter, IsolatedE5EmbeddingAdapter
from rag_access_guard_api.adapters.model_tokens import ModelTokenCounter
from rag_access_guard_api.adapters.ollama import OllamaAdapter
from rag_access_guard_api.config import Settings
from rag_access_guard_api.schemas.generation import GenerationUnavailable
from rag_access_guard_api.services.evaluation_identity import profile_hash
from rag_access_guard_api.services.evidence_selection import NO_EVIDENCE, extractive_messages
from rag_access_guard_api.services.model_profiles import get_model_profile, profile_for_manifest
from rag_access_guard_api.services.origin_context import SYNTHETIC_LABEL

PROFILE: Final = "qwen25-extractive-small-demo-v1"
JSON: Final = TypeAdapter[JsonValue](JsonValue)
OPTIONS: Final = {
    "num_ctx": 4096,
    "num_predict": 512,
    "temperature": 0,
    "seed": 42,
    "num_gpu": 0,
    "num_batch": 64,
}


def select_counter(monkeypatch: pytest.MonkeyPatch) -> ModelTokenCounter:
    profile = get_model_profile(PROFILE)
    monkeypatch.setenv("RAG_ACCESS_GUARD_MODEL_PROFILE", PROFILE)
    monkeypatch.setenv(
        "RAG_ACCESS_GUARD_MODEL_TOKENIZER_PATH",
        str(Path(".cache/qwen3") / profile.manifest.tokenizer_revision / "tokenizer.json"),
    )
    return ModelTokenCounter(profile.manifest)


def test_small_extractive_identity_is_distinct_when_reusing_the_verified_artifact() -> None:
    # Given the previously published instruct bundle.
    instruct = get_model_profile("qwen25-instruct-small-demo-v1")
    # When the operator selects the closed extractive profile.
    profile = get_model_profile(PROFILE)
    # Then only the answer contract, renderer and resource options change.
    expected = instruct.manifest.model_dump()
    expected["prompt_revision"] = "rag-excerpts-ru-v1"
    assert profile.manifest.model_dump() == expected
    assert profile.options() == OPTIONS
    assert profile.tokenizer_sha256 == instruct.tokenizer_sha256
    assert profile.template_sha256 == instruct.template_sha256
    assert profile.runtime_version == "0.34.4"
    assert profile.renderer_revision == "ollama-qwen25-excerpts-ru-v1"
    assert profile_for_manifest(profile.manifest) is profile
    assert profile_hash(profile) != profile_hash(instruct)
    assert (
        profile_hash(instruct) == "19c5bce89a419451c02f829c2929694b34c05ad22a99eb4f5e41c92aa36f017a"
    )
    assert Settings(model_profile=profile.profile_id).model_profile == PROFILE
    assert Settings().model_profile == "qwen3-thinking-legacy-v1"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model_name", "client-selected-model"),
        ("model_digest", "0" * 64),
        ("tokenizer_revision", "unknown"),
        ("tokenizer_id", "unknown"),
        ("model_system_sha256", "0" * 64),
        ("prompt_revision", "rag-instruct-ru-origin-v2"),
        ("context_window", 2048),
        ("max_output_tokens", 513),
        ("unknown", True),
    ],
)
def test_small_extractive_manifest_denies_changes_when_metadata_is_unpinned(
    field: str, *, value: str | int | bool
) -> None:
    # Given an altered operator bundle.
    manifest = get_model_profile(PROFILE).manifest
    fields = manifest.model_dump()
    fields[field] = value
    # When parsing its metadata, then fail closed.
    with pytest.raises(ValidationError):
        _ = type(manifest).model_validate(fields)


def test_small_extractive_renderer_matches_qwen25_when_explicit_messages_override_system(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given source passages and the verified Qwen2.5 tokenizer.
    counter = select_counter(monkeypatch)
    context = context_for(PASSAGE)
    profile = get_model_profile(PROFILE)
    messages = extractive_messages(user_input="QUESTION 👋", system_supplied_context=context)
    expected = (
        f"<|im_start|>system\n{messages[0]['content']}<|im_end|>\n"
        f"<|im_start|>user\n{messages[1]['content']}<|im_end|>\n"
        "<|im_start|>assistant\n"
    )
    # When rendering the extractive request.
    rendered = profile.render(user_input="QUESTION 👋", system_supplied_context=context)
    # Then the exact runtime ChatML prefix has no inherited system or thinking prefix.
    assert rendered == expected
    assert profile.messages(user_input="QUESTION 👋", system_supplied_context=context) == messages
    assert counter.count_request(
        user_input="QUESTION 👋", system_supplied_context=context
    ) == counter.count(expected)
    instruct = get_model_profile("qwen25-instruct-small-demo-v1")
    assert counter.identity != ModelTokenCounter(instruct.manifest).identity


@pytest.mark.anyio
@pytest.mark.parametrize(
    "selection",
    [
        '{"paragraph_ids":[0]}',
        '{"paragraph_ids":[]}',
        '{"paragraph_ids":[999]}',
        '{"paragraph_ids":[true]}',
        '{"paragraph_ids":[0,0]}',
        '{"paragraph_ids":[-1]}',
        '{"paragraph_ids":["0"]}',
        '{"paragraph_ids":[0],"answer":"FABRICATED"}',
        "FABRICATED",
    ],
)
async def test_small_extractive_adapter_releases_exact_quotes_when_selection_is_valid(
    monkeypatch: pytest.MonkeyPatch, selection: str
) -> None:
    # Given an explicit profile and schema-constrained wire response.
    _ = select_counter(monkeypatch)

    def respond(request: httpx2.Request) -> httpx2.Response:
        body = JSON.validate_json(request.content)
        assert isinstance(body, dict)
        assert body["model"] == "qwen2.5:1.5b-instruct-q4_K_M"
        assert body["options"] == OPTIONS
        assert body["stream"] is False
        assert "think" not in body
        schema = body["format"]
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
        adapter = OllamaAdapter(client)
        # When the model returns paragraph IDs, then release only host-rendered text.
        if selection in {'{"paragraph_ids":[0]}', '{"paragraph_ids":[]}'}:
            answer = await adapter.generate(
                user_input="QUESTION_WITH_USER_PASTE", system_supplied_context=context_for(PASSAGE)
            )
            assert answer == (
                f"{SYNTHETIC_LABEL}\n\n{PASSAGE}"
                if selection == '{"paragraph_ids":[0]}'
                else NO_EVIDENCE
            )
        else:
            with pytest.raises(GenerationUnavailable):
                _ = await adapter.generate(
                    user_input="QUESTION", system_supplied_context=context_for(PASSAGE)
                )


@pytest.mark.parametrize(
    ("runtime_platform", "isolated"),
    [(("win32", 2**63 - 1), True), (("win32", 2**31 - 1), False), (("linux", 2**63 - 1), False)],
)
def test_small_extractive_uses_isolated_e5_when_windows64(
    monkeypatch: pytest.MonkeyPatch, runtime_platform: tuple[str, int], *, isolated: bool
) -> None:
    # Given the Windows64 operator selection.
    monkeypatch.setenv("RAG_ACCESS_GUARD_MODEL_PROFILE", PROFILE)
    monkeypatch.setattr(sys, "platform", runtime_platform[0])
    monkeypatch.setattr(sys, "maxsize", runtime_platform[1])
    # When creating the adapter, then use the existing isolated lifecycle.
    adapter = embeddings.get_embedding_adapter()
    assert isinstance(adapter, E5EmbeddingAdapter)
    assert isinstance(adapter, IsolatedE5EmbeddingAdapter) is isolated
    assert not adapter.retain_model
