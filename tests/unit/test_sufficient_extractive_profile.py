import sys
from pathlib import Path
from typing import Final

import httpx2
import pytest
from pydantic import JsonValue, TypeAdapter
from tests.unit.test_extractive_model_profile import PASSAGE, context_for

from rag_access_guard_api.adapters import embeddings
from rag_access_guard_api.adapters.embeddings import IsolatedE5EmbeddingAdapter
from rag_access_guard_api.adapters.model_tokens import ModelTokenCounter
from rag_access_guard_api.adapters.ollama import OllamaAdapter
from rag_access_guard_api.config import Settings
from rag_access_guard_api.schemas.generation import GenerationUnavailable
from rag_access_guard_api.services.evaluation_identity import profile_hash
from rag_access_guard_api.services.evidence_context import evidence_passages
from rag_access_guard_api.services.evidence_selection import NO_EVIDENCE, selection_schema
from rag_access_guard_api.services.model_profiles import get_model_profile, profile_for_manifest
from rag_access_guard_api.services.origin_context import SYNTHETIC_LABEL

PROFILE: Final = "qwen25-extractive-small-demo-v2"


def test_wire_grammar_forbids_ids_when_evidence_is_insufficient() -> None:
    # Given one supplied passage and the opt-in constrained generator.
    entries = evidence_passages(context_for(PASSAGE))
    # When compiling its wire grammar.
    schema = selection_schema(entries, sufficient=True)
    # Then each decision admits only its consistent ID cardinality.
    branches = schema["oneOf"]
    assert isinstance(branches, list)
    assert len(branches) == 2
    for branch, decision, minimum, maximum in (
        (branches[0], "complete", 1, 1),
        (branches[1], "insufficient", 0, 0),
    ):
        assert isinstance(branch, dict)
        assert branch["additionalProperties"] is False
        assert branch["required"] == ["decision", "paragraph_ids"]
        properties = branch["properties"]
        assert isinstance(properties, dict)
        assert list(properties) == ["decision", "paragraph_ids"]
        assert properties["decision"] == {"const": decision}
        ids = properties["paragraph_ids"]
        assert isinstance(ids, dict)
        assert ids["minItems"] == minimum
        assert ids["maxItems"] == maximum


def select_counter(monkeypatch: pytest.MonkeyPatch) -> ModelTokenCounter:
    profile = get_model_profile(PROFILE)
    monkeypatch.setenv("RAG_ACCESS_GUARD_MODEL_PROFILE", PROFILE)
    monkeypatch.setenv(
        "RAG_ACCESS_GUARD_MODEL_TOKENIZER_PATH",
        str(Path(".cache/qwen3") / profile.manifest.tokenizer_revision / "tokenizer.json"),
    )
    return ModelTokenCounter(profile.manifest)


def test_sufficiency_profile_binds_a_distinct_contract_to_existing_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given the accepted small-model bundle and its local tokenizer.
    original = get_model_profile("qwen25-extractive-small-demo-v1")
    # When selecting the opt-in sufficiency contract.
    current = get_model_profile(PROFILE)
    counter = select_counter(monkeypatch)
    # Then hardware/model settings remain fixed while integrity distinguishes the contract.
    expected = original.manifest.model_dump()
    expected["prompt_revision"] = "rag-excerpts-ru-v2"
    assert current.manifest.model_dump() == expected
    assert current.options() == original.options()
    assert current.tokenizer_sha256 == original.tokenizer_sha256
    assert current.template_sha256 == original.template_sha256
    assert current.runtime_version == original.runtime_version
    assert current.renderer_revision == "ollama-qwen25-excerpts-ru-v2"
    assert profile_for_manifest(current.manifest) is current
    assert profile_hash(current) != profile_hash(original)
    assert (
        profile_hash(original) == "55c8ee75a435c4c69a1d08d36e97eed22a397398356674dbabd82ef6048c068d"
    )
    assert counter.identity != ModelTokenCounter(original.manifest).identity
    assert Settings(model_profile=PROFILE).model_profile == PROFILE
    monkeypatch.delenv("RAG_ACCESS_GUARD_MODEL_PROFILE")
    assert Settings().model_profile == "qwen3-thinking-legacy-v1"


def test_sufficiency_profile_keeps_isolated_e5_on_windows64(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given the new profile on the accepted lightweight platform.
    monkeypatch.setenv("RAG_ACCESS_GUARD_MODEL_PROFILE", PROFILE)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "maxsize", 2**63 - 1)
    # When constructing its embedding adapter, then no resident model is introduced.
    adapter = embeddings.get_embedding_adapter()
    assert isinstance(adapter, IsolatedE5EmbeddingAdapter)
    assert not adapter.retain_model


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("selection", "expected"),
    [
        ('{"decision":"complete","paragraph_ids":[0]}', f"{SYNTHETIC_LABEL}\n\n{PASSAGE}"),
        ('{"decision":"insufficient","paragraph_ids":[]}', NO_EVIDENCE),
        ('{"decision":"complete","paragraph_ids":[]}', None),
        ('{"decision":"insufficient","paragraph_ids":[0]}', None),
        ('{"decision":"complete","paragraph_ids":[0,0]}', None),
        ('{"decision":"complete","paragraph_ids":[999]}', None),
        ('{"decision":"complete","paragraph_ids":[true]}', None),
        ('{"decision":"complete","paragraph_ids":[-1]}', None),
        ('{"decision":"complete","paragraph_ids":["0"]}', None),
        ('{"decision":"maybe","paragraph_ids":[0]}', None),
        ('{"paragraph_ids":[0]}', None),
        ('{"decision":"complete","paragraph_ids":[0],"answer":"INVENTED"}', None),
    ],
)
async def test_model_decision_controls_exact_quote_or_refusal_in_one_call(
    monkeypatch: pytest.MonkeyPatch, selection: str, expected: str | None
) -> None:
    _ = select_counter(monkeypatch)
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        payload = TypeAdapter[JsonValue](JsonValue).validate_json(request.content)
        assert isinstance(payload, dict)
        assert payload["model"] == "qwen2.5:1.5b-instruct-q4_K_M"
        schema = payload["format"]
        assert isinstance(schema, dict)
        assert "oneOf" in schema
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
        if expected is None:
            with pytest.raises(GenerationUnavailable):
                _ = await adapter.generate(
                    user_input="QUESTION", system_supplied_context=context_for(PASSAGE)
                )
        else:
            answer = await adapter.generate(
                user_input="QUESTION", system_supplied_context=context_for(PASSAGE)
            )
            assert answer == expected
    assert len(requests) == 1
