from pathlib import Path

import httpx2
import pytest
from tests.unit.test_extractive_model_profile import context_for
from tests.unit.test_small_extractive_profile import PROFILE, select_counter

from rag_access_guard_api.adapters.model_tokens import ModelTokenCounter
from rag_access_guard_api.adapters.ollama import OllamaAdapter
from rag_access_guard_api.schemas.generation import GenerationUnavailable
from rag_access_guard_api.services.evaluation_identity import profile_hash
from rag_access_guard_api.services.model_profiles import (
    ModelProfile,
    SmallExtractiveOptions,
    get_model_profile,
)
from rag_access_guard_api.services.origin_context import SYNTHETIC_LABEL


@pytest.mark.anyio
@pytest.mark.parametrize("request_tokens", [3584, 3585])
async def test_small_extractive_denies_request_when_reserved_output_exceeds_context(
    monkeypatch: pytest.MonkeyPatch, request_tokens: int
) -> None:
    # Given exact real-tokenizer requests on either side of the 4096 - 512 boundary.
    counter = select_counter(monkeypatch)
    baseline = counter.count_request(
        user_input="QUESTION", system_supplied_context=context_for("x")
    )
    context = context_for("x" + " x" * (request_tokens - baseline))
    assert (
        counter.count_request(user_input="QUESTION", system_supplied_context=context)
        == request_tokens
    )
    assert counter.count(context) < 5000
    calls: list[str] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.url.path)
        return httpx2.Response(
            200,
            json={
                "done": True,
                "done_reason": "stop",
                "message": {"role": "assistant", "content": '{"paragraph_ids":[]}'},
            },
        )

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(respond), base_url="http://127.0.0.1:11435"
    ) as client:
        # When sending the bounded request, then only the exact fitting request reaches transport.
        adapter = OllamaAdapter(client)
        if request_tokens == 3584:
            _ = await adapter.generate(user_input="QUESTION", system_supplied_context=context)
            assert calls == ["/api/chat"]
        else:
            with pytest.raises(GenerationUnavailable):
                _ = await adapter.generate(user_input="QUESTION", system_supplied_context=context)
            assert calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("answer_tokens", [512, 513])
async def test_small_extractive_denies_quote_when_host_rendered_output_exceeds_budget(
    monkeypatch: pytest.MonkeyPatch, answer_tokens: int
) -> None:
    # Given exact quotation sizes including the retained Russian origin label.
    counter = select_counter(monkeypatch)
    baseline = counter.count(f"{SYNTHETIC_LABEL}\n\nx")
    passage = "x" + " x" * (answer_tokens - baseline)
    expected = f"{SYNTHETIC_LABEL}\n\n{passage}"
    assert counter.count(expected) == answer_tokens

    def respond(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "done": True,
                "done_reason": "stop",
                "message": {"role": "assistant", "content": '{"paragraph_ids":[0]}'},
            },
        )

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(respond), base_url="http://127.0.0.1:11435"
    ) as client:
        # When rendering the selection, then the complete quotation must fit the output budget.
        adapter = OllamaAdapter(client)
        if answer_tokens == 512:
            assert (
                await adapter.generate(
                    user_input="QUESTION", system_supplied_context=context_for(passage)
                )
                == expected
            )
        else:
            with pytest.raises(GenerationUnavailable):
                _ = await adapter.generate(
                    user_input="QUESTION", system_supplied_context=context_for(passage)
                )


def test_small_extractive_counter_denies_tokenizer_when_bytes_are_corrupt(tmp_path: Path) -> None:
    # Given unverified local tokenizer bytes.
    path = tmp_path / "tokenizer.json"
    _ = path.write_bytes(b"{}")
    # When constructing the counter, then fail before tokenization.
    with pytest.raises(GenerationUnavailable):
        _ = ModelTokenCounter(get_model_profile(PROFILE).manifest, path)


def test_small_extractive_identity_changes_when_compute_options_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given the pinned profile and token-counter identities.
    counter = select_counter(monkeypatch)
    profile = get_model_profile(PROFILE)
    original_counter = counter.identity
    original_profile = profile_hash(profile)

    def changed_options(_profile: ModelProfile) -> SmallExtractiveOptions:
        return {
            "num_ctx": 4096,
            "num_predict": 512,
            "temperature": 0,
            "seed": 42,
            "num_gpu": 0,
            "num_batch": 128,
        }

    # When the compute batch changes, then integrity binds to that different execution.
    monkeypatch.setattr(ModelProfile, "options", changed_options)
    assert counter.identity != original_counter
    assert profile_hash(profile) != original_profile
