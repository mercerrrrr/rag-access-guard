from collections.abc import Generator
from pathlib import Path
from typing import Final

import httpx2
import pytest
from pydantic import JsonValue, TypeAdapter
from tests.unit.test_extractive_model_profile import PASSAGE, context_for

from rag_access_guard_api.adapters.inference_runtime import InferenceRuntime
from rag_access_guard_api.adapters.model_tokens import ModelTokenCounter
from rag_access_guard_api.adapters.ollama import OllamaAdapter
from rag_access_guard_api.schemas.generation import GenerationUnavailable
from rag_access_guard_api.services.evaluation_identity import profile_hash
from rag_access_guard_api.services.evidence_context import evidence_passages
from rag_access_guard_api.services.evidence_selection import NO_EVIDENCE
from rag_access_guard_api.services.model_profiles import get_model_profile, profile_for_manifest
from rag_access_guard_api.services.origin_context import SYNTHETIC_LABEL

PROFILE: Final = "qwen25-extractive-small-demo-v3"


@pytest.fixture
def runtime() -> Generator[InferenceRuntime]:
    value = InferenceRuntime()
    try:
        yield value
    finally:
        value.close()


def test_mask_bundle_keeps_artifacts_but_changes_contract_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given the immutable installed small model and its previous bundle.
    old = get_model_profile("qwen25-extractive-small-demo-v2")
    # When selecting the new, explicit mask contract.
    new = get_model_profile(PROFILE)
    # Then weights and hardware settings stay pinned and both identities remain distinct.
    assert new.manifest.model_digest == old.manifest.model_digest
    assert new.options() == old.options()
    assert new.tokenizer_sha256 == old.tokenizer_sha256
    assert new.template_sha256 == old.template_sha256
    assert profile_for_manifest(new.manifest) is new
    assert profile_hash(new) != profile_hash(old)
    assert profile_hash(old) == "abf75b8b078539fdb08d39231da95220c84f991c3b5fd28c5e6f006af7f13b5c"
    monkeypatch.setenv(
        "RAG_ACCESS_GUARD_MODEL_TOKENIZER_PATH",
        str(Path(".cache/qwen3") / new.manifest.tokenizer_revision / "tokenizer.json"),
    )
    assert ModelTokenCounter(new.manifest).identity != ModelTokenCounter(old.manifest).identity


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ('{"supported":[true]}', f"{SYNTHETIC_LABEL}\n\n{PASSAGE}"),
        ('{"supported":[false]}', NO_EVIDENCE),
        ('{"supported":[]}', None),
        ('{"supported":[true,false]}', None),
        ('{"supported":["true"]}', None),
        ('{"supported":[1]}', None),
        ('{"supported":[null]}', None),
        ('{"supported":[true],"answer":"UNTRUSTED PROSE"}', None),
    ],
)
async def test_mask_releases_only_one_canonical_quote_or_refusal(
    monkeypatch: pytest.MonkeyPatch,
    runtime: InferenceRuntime,
    content: str,
    expected: str | None,
) -> None:
    # Given one managed passage and a bounded, observable model transport.
    profile = get_model_profile(PROFILE)
    monkeypatch.setenv(
        "RAG_ACCESS_GUARD_MODEL_TOKENIZER_PATH",
        str(Path(".cache/qwen3") / profile.manifest.tokenizer_revision / "tokenizer.json"),
    )
    calls: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        return httpx2.Response(
            200, json={"done": True, "message": {"role": "assistant", "content": content}}
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        adapter = OllamaAdapter(client, manifest=profile.manifest, runtime=runtime)
        # When generating from the selected profile.
        if expected is None:
            with pytest.raises(GenerationUnavailable):
                _ = await adapter.generate(
                    user_input="QUESTION", system_supplied_context=context_for(PASSAGE)
                )
        else:
            assert (
                await adapter.generate(
                    user_input="QUESTION", system_supplied_context=context_for(PASSAGE)
                )
                == expected
            )
    # Then one call uses a strict Boolean mask of the complete expected cardinality.
    assert len(calls) == 1
    payload = TypeAdapter[dict[str, JsonValue]](dict[str, JsonValue]).validate_json(
        calls[0].content
    )
    schema = payload["format"]
    assert isinstance(schema, dict)
    properties = schema["properties"]
    assert isinstance(properties, dict)
    assert properties["supported"] == {
        "type": "array",
        "items": {"type": "boolean"},
        "minItems": 1,
        "maxItems": 1,
    }


@pytest.mark.anyio
async def test_mask_does_not_silently_truncate_more_than_three_selected_passages(
    monkeypatch: pytest.MonkeyPatch, runtime: InferenceRuntime
) -> None:
    # Given five distinct canonical passages and five positive model flags.
    profile = get_model_profile(PROFILE)
    monkeypatch.setenv(
        "RAG_ACCESS_GUARD_MODEL_TOKENIZER_PATH",
        str(Path(".cache/qwen3") / profile.manifest.tokenizer_revision / "tokenizer.json"),
    )
    context = context_for("\n\n".join(f"{i}. {PASSAGE}" for i in range(5)))
    assert len(evidence_passages(context)) == 5

    def respond(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "done": True,
                "message": {
                    "role": "assistant",
                    "content": '{"supported":[true,true,true,true,true]}',
                },
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        adapter = OllamaAdapter(client, manifest=profile.manifest, runtime=runtime)
        # When a result exceeds the three-quotation contract, then deny the whole result.
        with pytest.raises(GenerationUnavailable):
            _ = await adapter.generate(user_input="QUESTION", system_supplied_context=context)
