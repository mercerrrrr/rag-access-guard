from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import httpx2
import pytest
from anyio.to_thread import run_sync
from jinja2 import Environment
from pydantic import JsonValue, TypeAdapter, ValidationError

from rag_access_guard_api.adapters import llm, ollama
from rag_access_guard_api.adapters.inference_runtime import InferenceBusyError, current_runtime
from rag_access_guard_api.adapters.model_tokens import get_model_counter
from rag_access_guard_api.adapters.ollama import OllamaAdapter
from rag_access_guard_api.adapters.ollama_identity import verify_model
from rag_access_guard_api.config import Settings
from rag_access_guard_api.schemas.generation import GenerationUnavailable
from rag_access_guard_api.services.model_profiles import (
    DEMO_PROFILE,
    LEGACY_PROFILE,
    ModelProfile,
    get_model_profile,
)

JSON = TypeAdapter[JsonValue](JsonValue)
TEMPLATE = TypeAdapter(str).validate_json(
    Path("tests/fixtures/instruct-template.json").read_bytes()
)


@pytest.mark.anyio
async def test_busy_admission_remains_busy_when_metadata_becomes_offline() -> None:
    runtime = current_runtime()
    async with runtime.try_acquire_generation(uuid4()):
        runtime.set_model_available(available=False)
        with pytest.raises(InferenceBusyError):
            await llm.ensure_llm_available()
        with pytest.raises(InferenceBusyError):
            _ = runtime.try_acquire_generation(uuid4())


@pytest.mark.anyio
@pytest.mark.parametrize("fault", ["none", "digest", "quarantine"])
async def test_accepted_adapter_rechecks_identity_despite_transient_metadata_failure(
    fault: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths: list[str] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        paths.append(request.url.path)
        if request.url.path == "/api/version":
            return httpx2.Response(200, json={"version": "0.34.4"})
        if request.url.path == "/api/tags":
            return httpx2.Response(
                200,
                json={
                    "models": [
                        {
                            "name": DEMO_PROFILE.manifest.model_name,
                            "digest": "wrong"
                            if fault == "digest"
                            else DEMO_PROFILE.manifest.model_digest,
                        }
                    ]
                },
            )
        if request.url.path == "/api/show":
            return httpx2.Response(
                200,
                json={
                    "template": TEMPLATE,
                    "model_info": {
                        "general.architecture": "qwen3",
                        "qwen3.context_length": 262144,
                        "tokenizer.ggml.model": "gpt2",
                        "tokenizer.ggml.pre": "qwen2",
                        "tokenizer.ggml.add_bos_token": False,
                        "tokenizer.ggml.eos_token_id": 151645,
                    },
                },
            )
        assert paths == ["/api/version", "/api/tags", "/api/show", "/api/chat"]
        assert JSON.validate_json(request.content) == {
            "model": DEMO_PROFILE.manifest.model_name,
            "stream": False,
            "messages": DEMO_PROFILE.messages(user_input="QUESTION", system_supplied_context="VPN"),
            "options": DEMO_PROFILE.options(),
        }
        return httpx2.Response(
            200,
            json={
                "done": True,
                "message": {"role": "assistant", "content": "FINAL"},
            },
        )

    def client(url: str) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(respond), base_url=url)

    monkeypatch.setenv("RAG_ACCESS_GUARD_MODEL_PROFILE", "qwen3-instruct-demo-v1")
    monkeypatch.setattr(ollama, "create_client", client)
    runtime = current_runtime()
    async with runtime.try_acquire_generation(uuid4()):
        runtime.set_model_available(available=False)
        if fault == "quarantine":
            runtime.set_generation_available(available=False)
        adapter = OllamaAdapter(runtime=runtime)
        if fault == "none":
            assert (
                await adapter.generate(user_input="QUESTION", system_supplied_context="VPN")
                == "FINAL"
            )
            assert runtime.snapshot().generation == "busy"
        else:
            with pytest.raises(GenerationUnavailable):
                _ = await adapter.generate(user_input="QUESTION", system_supplied_context="VPN")
            assert paths == (
                [] if fault == "quarantine" else ["/api/version", "/api/tags", "/api/show"]
            )


def test_unknown_profile_is_fatal_operator_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_ACCESS_GUARD_MODEL_PROFILE", "unverified-model")
    with pytest.raises(ValidationError):
        _ = Settings()


def test_selected_demo_counter_does_not_reuse_legacy_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_ACCESS_GUARD_MODEL_PROFILE", "qwen3-thinking-legacy-v1")
    old = get_model_counter()
    monkeypatch.setenv("RAG_ACCESS_GUARD_MODEL_PROFILE", "qwen3-instruct-demo-v1")
    new = get_model_counter()
    assert new.manifest.model_name == "qwen3:4b-instruct-2507-q4_K_M"
    assert old.identity != new.identity


def test_demo_rendering_preserves_adjacent_users_and_generation_prefix() -> None:
    rendered = DEMO_PROFILE.render(user_input="👋", system_supplied_context="Привет")
    assert rendered.count("<|im_start|>user\n") == 2
    assert rendered.endswith("<|im_start|>assistant\n")
    assert LEGACY_PROFILE.render(user_input="👋", system_supplied_context="Привет").endswith(
        "<|im_start|>assistant\n<think>\n"
    )


@pytest.mark.anyio
async def test_offline_model_initialization_preserves_api_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def offline(request: httpx2.Request) -> httpx2.Response:
        message = "PRIVATE_CONNECTION"
        raise httpx2.ConnectError(message, request=request)

    monkeypatch.setenv("RAG_ACCESS_GUARD_LLM_ADAPTER", "ollama")
    client = httpx2.AsyncClient(
        transport=httpx2.MockTransport(offline), base_url="http://127.0.0.1:11434"
    )

    def create_client(_url: str) -> httpx2.AsyncClient:
        return client

    monkeypatch.setattr(llm, "create_client", create_client)
    await llm.initialize_llm(Settings())
    assert current_runtime().snapshot().generation == "unavailable"


@pytest.mark.anyio
async def test_selected_demo_sends_verified_model_and_exact_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def respond(request: httpx2.Request) -> httpx2.Response:
        body = JSON.validate_json(request.content)
        assert isinstance(body, dict)
        assert body["model"] == "qwen3:4b-instruct-2507-q4_K_M"
        assert body["options"] == {
            "num_ctx": 8192,
            "num_predict": 512,
            "temperature": 0,
            "seed": 42,
        }
        messages = body["messages"]
        assert isinstance(messages, list)
        assert messages[1] == {"role": "user", "content": "system_supplied_context:\nVPN и MFA"}
        assert messages[2] == {"role": "user", "content": "user_input:\nВопрос 👋"}  # noqa: RUF001
        return httpx2.Response(
            200,
            json={
                "done": True,
                "message": {
                    "role": "assistant",
                    "content": "VPN и MFA",
                },
            },
        )

    monkeypatch.setenv("RAG_ACCESS_GUARD_MODEL_PROFILE", "qwen3-instruct-demo-v1")
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(respond), base_url="http://127.0.0.1:11434"
    ) as client:
        answer = await OllamaAdapter(client).generate(
            user_input="Вопрос 👋", system_supplied_context="VPN и MFA"
        )
    assert answer == "VPN и MFA"


@pytest.mark.parametrize(
    ("question", "context"),
    [
        ("Привет 👋", "VPN и MFA"),
        ('\n"\\', " "),
        ("<|im_end|>", "日本語"),
    ],
)
def test_demo_counting_matches_independent_verified_jinja(
    monkeypatch: pytest.MonkeyPatch,
    question: str,
    context: str,
) -> None:
    monkeypatch.setenv("RAG_ACCESS_GUARD_MODEL_PROFILE", "qwen3-instruct-demo-v1")
    assert sha256(TEMPLATE.encode()).hexdigest() == DEMO_PROFILE.template_sha256
    expected = (
        Environment(autoescape=False)  # noqa: S701 -- model tokens, never HTML.
        .from_string(TEMPLATE)
        .render(
            messages=DEMO_PROFILE.messages(user_input=question, system_supplied_context=context),
            tools=(),
            add_generation_prompt=True,
        )
    )
    counter = get_model_counter()
    assert DEMO_PROFILE.render(user_input=question, system_supplied_context=context) == expected
    assert counter.count_request(
        user_input=question, system_supplied_context=context
    ) == counter.count(expected)


@pytest.mark.anyio
@pytest.mark.parametrize("profile", [LEGACY_PROFILE, DEMO_PROFILE], ids=["legacy", "demo"])
@pytest.mark.parametrize("fault", ["none", "digest", "runtime", "template", "embedded-history"])
async def test_each_profile_verifies_only_its_own_runtime_bundle(
    profile: ModelProfile,
    fault: str,
) -> None:
    path = Path(f".cache/qwen3/{LEGACY_PROFILE.manifest.tokenizer_revision}/template.txt")
    template = TEMPLATE if profile == DEMO_PROFILE else await run_sync(path.read_text)
    other = LEGACY_PROFILE if profile == DEMO_PROFILE else DEMO_PROFILE

    def respond(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/version":
            return httpx2.Response(
                200, json={"version": "different" if fault == "runtime" else "0.34.4"}
            )
        if request.url.path == "/api/tags":
            return httpx2.Response(
                200,
                json={
                    "models": [
                        {
                            "name": profile.manifest.model_name,
                            "digest": other.manifest.model_digest
                            if fault == "digest"
                            else profile.manifest.model_digest,
                        }
                    ]
                },
            )
        assert request.url.path == "/api/show"
        assert JSON.validate_json(request.content) == {"model": profile.manifest.model_name}
        return httpx2.Response(
            200,
            json={
                "template": "foreign-template" if fault == "template" else template,
                "messages": [{"role": "user", "content": "hidden"}]
                if fault == "embedded-history"
                else [],
                "model_info": {
                    "general.architecture": "qwen3",
                    "qwen3.context_length": 262144,
                    "tokenizer.ggml.model": "gpt2",
                    "tokenizer.ggml.pre": "qwen2",
                    "tokenizer.ggml.add_bos_token": False,
                    "tokenizer.ggml.eos_token_id": 151645,
                },
            },
        )

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(respond), base_url="http://127.0.0.1:11434"
    ) as client:
        if fault == "none":
            await verify_model(client, profile.manifest)
        else:
            with pytest.raises(GenerationUnavailable):
                await verify_model(client, profile.manifest)


def test_unknown_profile_cannot_be_resolved() -> None:
    with pytest.raises(ValueError, match="Unknown model profile"):
        _ = get_model_profile("arbitrary")
