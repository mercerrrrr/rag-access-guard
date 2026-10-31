from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from anyio.to_thread import run_sync

from rag_access_guard_api.adapters import llm
from rag_access_guard_api.adapters.relevance import EvidenceRoute
from rag_access_guard_api.config import Settings
from rag_access_guard_api.schemas.chat import MessageRequest
from rag_access_guard_api.schemas.generation import GenerationUnavailable
from rag_access_guard_api.services import chat, generation_plan
from rag_access_guard_api.services.model_profiles import ProfileId, get_model_profile
from tests.support.chat_generation import CapturingLLM, ChatCase


@dataclass
class RelevanceCase:
    chat: ChatCase
    route: EvidenceRoute = "strict"
    hold: bool = False
    fail: bool = False
    entered: Event = field(default_factory=Event)
    resume: Event = field(default_factory=Event)
    profiles: list[ProfileId | None] = field(default_factory=list)


@pytest.fixture
def relevance_case(
    chat_case: ChatCase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> RelevanceCase:
    settings = Settings(
        auth_origin="http://127.0.0.1:4173",
        loopback_development=True,
        llm_adapter="ollama",
        model_profile="qwen25-extractive-small-demo-v3",
        relevance_config_path=tmp_path / "relevance.json",
    )
    case = RelevanceCase(chat_case)

    async def assess(
        config_path: Path, *, user_input: str, system_supplied_context: str
    ) -> EvidenceRoute:
        assert config_path == settings.relevance_config_path
        assert user_input == "QUESTION"
        assert "PROTECTED_SYNTHETIC" in system_supplied_context
        case.entered.set()
        if case.hold and not await run_sync(case.resume.wait, 10):
            raise TimeoutError
        if case.fail:
            raise GenerationUnavailable
        return case.route

    def adapter(profile_id: ProfileId | None = None) -> CapturingLLM:
        case.profiles.append(profile_id)
        return case.chat.model

    monkeypatch.setattr(chat, "Settings", lambda: settings)
    monkeypatch.setattr(generation_plan, "assess_relevance", assess)
    monkeypatch.setattr(llm, "get_llm_adapter", adapter)
    profile = get_model_profile(settings.model_profile)
    monkeypatch.setenv(
        "RAG_ACCESS_GUARD_MODEL_TOKENIZER_PATH",
        str(Path(".cache/qwen3") / profile.manifest.tokenizer_revision / "tokenizer.json"),
    )
    return case


@pytest.mark.parametrize(
    ("route", "profile"),
    [
        ("strict", "qwen25-extractive-small-demo-v3"),
        ("sufficient", "qwen25-extractive-small-demo-v2"),
    ],
)
def test_selected_prompt_is_counted_and_released_with_real_policy(
    relevance_case: RelevanceCase, route: EvidenceRoute, profile: ProfileId
) -> None:
    relevance_case.route = route
    result = relevance_case.chat.send(
        MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="QUESTION")
    )
    assert result.turn.answer == relevance_case.chat.model.body
    assert result.turn.sources
    assert relevance_case.profiles == [profile]
    assert relevance_case.chat.model.call_count == 1
    assert relevance_case.chat.read().turns[0].answer == result.turn.answer


def test_no_evidence_finishes_without_a_generation_call(relevance_case: RelevanceCase) -> None:
    relevance_case.route = "none"
    result = relevance_case.chat.send(
        MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="QUESTION")
    )
    assert result.turn.state == "neutral"
    assert result.turn.answer is None
    assert result.turn.sources == ()
    assert relevance_case.profiles == []
    assert relevance_case.chat.model.call_count == 0


def test_relevance_failure_is_unavailable_without_model_or_sources(
    relevance_case: RelevanceCase,
) -> None:
    relevance_case.fail = True
    result = relevance_case.chat.send(
        MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="QUESTION")
    )
    assert result.turn.state == "neutral"
    assert result.turn.answer is None
    assert result.turn.sources == ()
    assert relevance_case.chat.model.call_count == 0


def test_revocation_during_relevance_prevents_generation(relevance_case: RelevanceCase) -> None:
    relevance_case.hold = True
    request = MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="QUESTION")
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(relevance_case.chat.send, request)
        try:
            assert relevance_case.entered.wait(10)
            relevance_case.chat.revoke()
        finally:
            relevance_case.resume.set()
        result = pending.result(10)
    assert result.turn.state == "neutral"
    assert result.turn.answer is None
    assert result.turn.sources == ()
    assert relevance_case.profiles == []
    assert relevance_case.chat.model.call_count == 0
    assert relevance_case.chat.read().turns[0].answer is None


def test_revocation_during_selected_generation_still_blocks_release(
    relevance_case: RelevanceCase,
) -> None:
    relevance_case.chat.model.hold = True
    request = MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="QUESTION")
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(relevance_case.chat.send, request)
        try:
            assert relevance_case.chat.model.entered.wait(10)
            relevance_case.chat.revoke()
        finally:
            relevance_case.chat.model.resume.set()
        result = pending.result(10)
    assert result.turn.state == "neutral"
    assert result.turn.answer is None
    assert result.turn.sources == ()
    assert relevance_case.profiles == ["qwen25-extractive-small-demo-v3"]
    assert relevance_case.chat.model.call_count == 1
    assert relevance_case.chat.read().turns[0].state == "neutral"
