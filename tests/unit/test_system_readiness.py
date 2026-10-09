from pathlib import Path
from typing import Final
from uuid import uuid4

import anyio
import httpx2
import pytest
from pydantic import TypeAdapter

from rag_access_guard_api.adapters import llm
from rag_access_guard_api.adapters.inference_runtime import InferenceBusyError, current_runtime
from rag_access_guard_api.config import Settings
from rag_access_guard_api.services import system_status
from rag_access_guard_api.services.model_profiles import DEMO_PROFILE
from rag_access_guard_api.services.system_status import SystemReadiness

TEMPLATE: Final = TypeAdapter(str).validate_json(
    Path("tests/fixtures/instruct-template.json").read_bytes()
)


@pytest.mark.anyio
@pytest.mark.parametrize("fault", ["offline", "timeout"])
async def test_accepted_busy_stays_ready_after_metadata_failure_or_timeout(
    fault: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths: list[str] = []

    async def respond(request: httpx2.Request) -> httpx2.Response:
        paths.append(request.url.path)
        if fault == "offline":
            message = "PRIVATE_METADATA_FAILURE"
            raise httpx2.ConnectError(message, request=request)
        await anyio.sleep_forever()
        raise AssertionError

    def client(url: str) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(respond), base_url=url)

    monkeypatch.setattr(system_status, "create_client", client)
    monkeypatch.setattr(system_status, "PROBE_SECONDS", 0.5)
    readiness = SystemReadiness(Settings(llm_adapter="ollama", retrieval_config_path=None))
    runtime = current_runtime()
    async with runtime.try_acquire_generation(uuid4()):
        assert (await readiness.snapshot()).model.state == "ready"
        assert paths == ["/api/version"]
        assert not runtime.model_available
        assert runtime.snapshot().generation == "busy"
        with pytest.raises(InferenceBusyError):
            await llm.ensure_llm_available()
        with pytest.raises(InferenceBusyError):
            _ = runtime.try_acquire_generation(uuid4())
        runtime.set_generation_available(available=False)
        assert (await readiness.snapshot()).model.state == "unavailable"
    assert runtime.snapshot().generation == "unavailable"


@pytest.fixture
def metadata_wire(monkeypatch: pytest.MonkeyPatch) -> list[str]:
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
                            "digest": DEMO_PROFILE.manifest.model_digest,
                        }
                    ]
                },
            )
        assert request.url.path == "/api/show"
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

    def client(url: str) -> httpx2.AsyncClient:
        assert url == "http://127.0.0.1:11434"
        return httpx2.AsyncClient(transport=httpx2.MockTransport(respond), base_url=url)

    monkeypatch.setenv("RAG_ACCESS_GUARD_MODEL_PROFILE", "qwen3-instruct-demo-v1")
    monkeypatch.setattr(system_status, "create_client", client)
    return paths


@pytest.mark.anyio
async def test_verified_local_readiness_ttl_does_not_generate_or_reopen_quarantine(
    metadata_wire: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [100.0]
    monkeypatch.setattr(system_status, "monotonic", lambda: clock[0])
    readiness = SystemReadiness(Settings(llm_adapter="ollama"))
    assert (await readiness.snapshot()).model.state == "ready"
    assert (await readiness.snapshot()).model.state == "ready"
    assert metadata_wire == ["/api/version", "/api/tags", "/api/show"]
    clock[0] = 111
    assert (await readiness.snapshot()).model.state == "ready"
    assert metadata_wire == ["/api/version", "/api/tags", "/api/show"] * 2
    current_runtime().set_generation_available(available=False)
    assert (await readiness.snapshot()).model.state == "unavailable"
    assert current_runtime().snapshot().generation == "unavailable"


@pytest.mark.anyio
async def test_offline_local_readiness_is_safe_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def respond(request: httpx2.Request) -> httpx2.Response:
        message = "PRIVATE_PATH_OR_CREDENTIAL"
        raise httpx2.ConnectError(message, request=request)

    def client(url: str) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(respond), base_url=url)

    monkeypatch.setattr(system_status, "create_client", client)
    result = await SystemReadiness(Settings(llm_adapter="ollama")).snapshot()
    assert result.model.state == "unavailable"
    assert "PRIVATE_PATH_OR_CREDENTIAL" not in result.model_dump_json()


@pytest.mark.anyio
async def test_unresponsive_metadata_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    paths: list[str] = []

    async def respond(_request: httpx2.Request) -> httpx2.Response:
        paths.append(_request.url.path)
        await anyio.sleep_forever()
        raise AssertionError

    def client(url: str) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(respond), base_url=url)

    monkeypatch.setattr(system_status, "create_client", client)
    monkeypatch.setattr(system_status, "PROBE_SECONDS", 0.05)
    result = await SystemReadiness(
        Settings(llm_adapter="ollama", retrieval_config_path=None)
    ).snapshot()
    assert result.model.state == "unavailable"
    assert paths == ["/api/version"]


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["disabled", "fake"])
async def test_nonlocal_modes_never_probe_model(
    mode: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(_url: str) -> httpx2.AsyncClient:
        raise AssertionError

    monkeypatch.setattr(system_status, "create_client", forbidden)
    monkeypatch.setenv("RAG_ACCESS_GUARD_LLM_ADAPTER", mode)
    result = await SystemReadiness(Settings()).snapshot()
    assert result.model.name is None
    assert result.model.state == ("test" if mode == "fake" else "disabled")


@pytest.mark.anyio
async def test_search_requires_artifacts_and_calibration(
    metadata_wire: list[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("RAG_ACCESS_GUARD_MODEL_PATH", str(tmp_path))
    result = await SystemReadiness(Settings(llm_adapter="ollama")).snapshot()
    assert result.model.state == "ready"
    assert result.search.state == "unavailable"
    assert metadata_wire == ["/api/version", "/api/tags", "/api/show"]
