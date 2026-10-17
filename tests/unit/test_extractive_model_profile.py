import json
from pathlib import Path
from typing import Final
from uuid import UUID

import httpx2
import pytest
from pydantic import JsonValue, TypeAdapter
from tests.unit.test_demo_ownership import config_for

from rag_access_guard_api.adapters.model_tokens import ModelTokenCounter, get_model_counter
from rag_access_guard_api.adapters.ollama import OllamaAdapter
from rag_access_guard_api.config import Settings
from rag_access_guard_api.demo.contracts import DemoConfig
from rag_access_guard_api.schemas.generation import GenerationUnavailable
from rag_access_guard_api.services.evaluation_identity import profile_hash
from rag_access_guard_api.services.model_profiles import get_model_profile
from rag_access_guard_api.services.origin_context import SYNTHETIC_LABEL

PROFILE: Final = "qwen35-extractive-demo-v1"
JSON: Final = TypeAdapter[JsonValue](JsonValue)
REF: Final = tuple(str(UUID(int=index)) for index in (1, 2, 3))
PASSAGE: Final = (
    "В учебном примере наставник объясняет упражнение, а оператор рассматривает права. "  # noqa: RUF001
    "Руководитель определяет приоритет; координатор подтверждает этапы."
)


def context_for(*texts: str) -> str:
    chunks = [[*REF[:2], str(UUID(int=index + 3)), text] for index, text in enumerate(texts)]
    encoded = json.dumps(
        {"version": 1, "chunks": chunks}, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    origins = [[*row[:3], "a" * 64, SYNTHETIC_LABEL] for row in chunks]
    return (
        encoded
        + "\norigin_annotations_v1:\n"
        + json.dumps(origins, ensure_ascii=False, separators=(",", ":"))
    )


def select_counter(monkeypatch: pytest.MonkeyPatch) -> ModelTokenCounter:
    profile = get_model_profile(PROFILE)
    monkeypatch.setenv("RAG_ACCESS_GUARD_MODEL_PROFILE", PROFILE)
    monkeypatch.setenv(
        "RAG_ACCESS_GUARD_MODEL_TOKENIZER_PATH",
        str(Path(".cache/qwen3") / profile.manifest.tokenizer_revision / "tokenizer.json"),
    )
    return get_model_counter()


def test_extractive_profile_is_opt_in_and_does_not_change_existing_identity(tmp_path: Path) -> None:
    profile = get_model_profile(PROFILE)
    assert profile.manifest.model_name == "qwen3.5:2b-q4_K_M"
    assert profile.options() == {"num_ctx": 4096, "num_predict": 512, "temperature": 0, "seed": 42}
    original = config_for(tmp_path)
    fields = original.model_dump(mode="python")
    fields["database_url"] = original.database_url
    fields["model_profile"] = PROFILE
    assert DemoConfig.model_validate(fields).model_profile == PROFILE
    assert Settings().model_profile == "qwen3-thinking-legacy-v1"
    assert (
        profile_hash(get_model_profile("qwen25-instruct-small-demo-v1"))
        == "19c5bce89a419451c02f829c2929694b34c05ad22a99eb4f5e41c92aa36f017a"
    )


@pytest.mark.anyio
async def test_adapter_releases_only_selected_source_text_not_model_prose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = select_counter(monkeypatch)
    question = "Какие роли? USER_PASTE_WITHOUT_PROVENANCE"
    context = context_for(PASSAGE)

    def respond(request: httpx2.Request) -> httpx2.Response:
        body = JSON.validate_json(request.content)
        assert isinstance(body, dict)
        assert body["model"] == "qwen3.5:2b-q4_K_M"
        assert body["think"] is False
        assert body["stream"] is False
        assert isinstance(body["format"], dict)
        assert body["format"]["additionalProperties"] is False
        assert body["format"]["required"] == ["paragraph_ids"]
        assert body["options"] == {
            "num_ctx": 4096,
            "num_predict": 512,
            "temperature": 0,
            "seed": 42,
        }
        messages = body["messages"]
        assert isinstance(messages, list)
        assert len(messages) == 2
        user_message = messages[1]
        assert isinstance(user_message, dict)
        assert isinstance(user_message["content"], str)
        projected, separator, user = user_message["content"].partition("\n\nuser_input:\n")
        assert separator
        assert user == question
        assert "USER_PASTE_WITHOUT_PROVENANCE" not in projected
        assert PASSAGE in projected
        assert not any(ref in projected for ref in REF)
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
        answer = await OllamaAdapter(client).generate(
            user_input=question, system_supplied_context=context
        )
    assert PASSAGE in answer
    assert SYNTHETIC_LABEL in answer
    assert "paragraph_ids" not in answer
    assert "USER_PASTE" not in answer
    assert counter.count(answer) <= 512
    profile = get_model_profile(PROFILE)
    messages = profile.messages(user_input=question, system_supplied_context=context)
    assert profile.render(user_input=question, system_supplied_context=context) == (
        f"<|im_start|>system\n{messages[0]['content']}<|im_end|>\n"
        f"<|im_start|>user\n{messages[1]['content']}<|im_end|>\n"
        "<|im_start|>assistant\n<think>\n\n</think>\n\n"
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "selection",
    [
        '{"paragraph_ids":[100]}',
        '{"paragraph_ids":[true]}',
        '{"paragraph_ids":[0,0]}',
        '{"paragraph_ids":[-1]}',
        '{"paragraph_ids":[0],"answer":"FABRICATED"}',
        '{"paragraph_ids":["0"]}',
    ],
)
async def test_invalid_selection_is_never_returned(
    monkeypatch: pytest.MonkeyPatch, selection: str
) -> None:
    _ = select_counter(monkeypatch)

    def respond(_: httpx2.Request) -> httpx2.Response:
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
        with pytest.raises(GenerationUnavailable):
            _ = await OllamaAdapter(client).generate(
                user_input="QUESTION", system_supplied_context=context_for(PASSAGE)
            )


@pytest.mark.anyio
async def test_exact_quotes_cannot_bypass_output_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    counter = select_counter(monkeypatch)
    context = context_for(*(f"Source {index}." + " word" * 250 for index in range(3)))
    assert counter.count(context) < 5000

    def respond(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "done": True,
                "done_reason": "stop",
                "message": {"role": "assistant", "content": '{"paragraph_ids":[0,1,2]}'},
            },
        )

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(respond), base_url="http://127.0.0.1:11435"
    ) as client:
        with pytest.raises(GenerationUnavailable):
            _ = await OllamaAdapter(client).generate(
                user_input="QUESTION", system_supplied_context=context
            )


@pytest.mark.anyio
async def test_selected_quote_cannot_bypass_plain_text_output_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = select_counter(monkeypatch)

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
        with pytest.raises(GenerationUnavailable):
            _ = await OllamaAdapter(client).generate(
                user_input="QUESTION",
                system_supplied_context=context_for("SOURCE_WITH_NUL\u0000TEXT"),
            )
