from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import httpx2
import pytest
from packages.rag_access_guard.tests.test_context_budget import candidate
from packages.rag_access_guard.tests.test_history import Reader, ref
from packages.rag_access_guard.tests.test_history_policy import chunk

from rag_access_guard import CandidateChunk, Guard, PreparedContext, PriorTurn, SourceRef
from rag_access_guard.context import bound_context, render_context
from rag_access_guard_api.adapters import llm
from rag_access_guard_api.adapters.model_tokens import ModelTokenCounter
from rag_access_guard_api.adapters.ollama import OllamaAdapter
from rag_access_guard_api.adapters.tokenizer import get_tokenizer
from rag_access_guard_api.schemas.chat import MessageRequest
from rag_access_guard_api.services.model_manifest import ModelManifest
from tests.support.chat import ChatHttp
from tests.support.chat_generation import ChatCase


@dataclass(frozen=True, slots=True)
class BudgetCase:
    counter: ModelTokenCounter
    chunks: tuple[CandidateChunk, ...]
    oversized_history: tuple[PriorTurn, ...]
    expected_refs: tuple[SourceRef, ...]


@pytest.fixture
def budget_case() -> BudgetCase:
    text = Path("tests/fixtures/context_budget.txt").read_text(encoding="utf-8")
    first = chunk()
    second = CandidateChunk(
        source_ref=ref(2),
        text=first.text,
        content_sha256=first.content_sha256,
        token_count=999999,
    )
    return BudgetCase(
        ModelTokenCounter(ModelManifest()),
        (first, second),
        (
            PriorTurn(
                turn_id=UUID(int=100),
                user_input="Старый вопрос",
                answer=text * 100,
                source_refs=(ref(3),),
                provenance_complete=True,
            ),
        ),
        (ref(1), ref(2)),
    )


@pytest.mark.anyio
async def test_serialized_context_never_exceeds_5000_tokens(budget_case: BudgetCase) -> None:
    prepared = await Guard(budget_case.counter).prepare_context(
        UUID(int=1),
        budget_case.chunks,
        budget_case.oversized_history,
        Reader(),
    )
    assert isinstance(prepared, PreparedContext)
    assert budget_case.counter.count(prepared.model_context) <= 5000
    assert budget_case.oversized_history[0].answer not in prepared.model_context
    assert prepared.source_refs == budget_case.expected_refs


@pytest.mark.parametrize("target", [5000, 5001])
def test_real_tokenizer_exact_context_boundary(target: int) -> None:
    counter = ModelTokenCounter(ModelManifest())
    overhead = counter.count(render_context((candidate(""),), ()))
    cases = tuple(candidate(" x" * n) for n in range(target - overhead - 3, target - overhead + 4))
    exact = next(c for c in cases if counter.count(render_context((c,), ())) == target)
    result = bound_context((exact,), (), token_counter=counter)
    assert counter.count(result.model_context) == (5000 if target == 5000 else 0)
    assert result.source_refs == ((exact.source_ref,) if target == 5000 else ())


@pytest.mark.parametrize("size", [1024, 1025])
def test_exact_question_token_limit_is_checked_before_reservation(
    chat_case: ChatCase,
    monkeypatch: pytest.MonkeyPatch,
    size: int,
) -> None:
    counter = ModelTokenCounter(ModelManifest())
    monkeypatch.setattr(llm, "get_token_counter", lambda: counter)
    question = "🧬" * 512 + " x" * (size - 1024)
    assert counter.count(question) == size
    assert get_tokenizer().embedding_input_tokens(question, kind="query") <= 512
    response = chat_case.client.post(
        f"/api/chat/threads/{chat_case.thread}/messages",
        json=MessageRequest(
            request_id=uuid4(), expected_thread_revision=0, user_input=question
        ).model_dump(mode="json"),
        headers=ChatHttp.csrf(chat_case.client),
    )
    assert response.status_code == (200 if size == 1024 else 422)
    assert chat_case.model.call_count == (1 if size == 1024 else 0)
    if size > 1024:
        assert response.json() == {"detail": {"code": "query_too_long"}}
        assert chat_case.read().turns == ()
    else:
        assert chat_case.model.inputs[0][0] == question


def test_real_tokenizer_bounds_complete_server_history(
    chat_case: ChatCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = ModelTokenCounter(ModelManifest())
    monkeypatch.setattr(llm, "get_token_counter", lambda: counter)
    for number in range(1, 6):
        chat_case.model.body = f"LONG_ANSWER_{number}:" + " x" * 1500
        result = chat_case.send(
            MessageRequest(
                request_id=uuid4(),
                expected_thread_revision=number - 1,
                user_input=f"CURRENT_{number}",
            )
        )
        assert result.turn.state == "available"
        assert {s.document_id for s in result.turn.sources} == {chat_case.document.id}
        assert counter.count(chat_case.model.inputs[-1][1]) <= 5000
    user, context = chat_case.model.inputs[-1]
    assert user == "CURRENT_5"
    assert "CURRENT_1" not in context
    assert "LONG_ANSWER_1:" not in context
    assert "CURRENT_4" in context
    assert "LONG_ANSWER_4:" in context
    assert "CURRENT_5" not in context


@pytest.mark.anyio
async def test_full_request_overflow_is_neutral_without_model_transport(
    chat_case: ChatCase,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    chat_case.model.body = "PRIVATE_HISTORY_MARKER" + " x" * 1500
    first = chat_case.send(
        MessageRequest(
            request_id=uuid4(),
            expected_thread_revision=0,
            user_input="FIRST",
        )
    )
    assert first.turn.state == "available"
    calls: list[str] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.url.path)
        return httpx2.Response(500)

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(respond),
        base_url="http://127.0.0.1:11434",
    ) as client:
        manifest = ModelManifest(context_window=2048, max_output_tokens=1024)
        adapter = OllamaAdapter(client, manifest=manifest)
        question = "🧬" * 512
        assert adapter.counter.count(question) == 1024
        assert (
            adapter.counter.count_request(user_input=question, system_supplied_context="")
            + manifest.max_output_tokens
            > manifest.context_window
        )
        monkeypatch.setattr(llm, "get_llm_adapter", lambda: adapter)
        monkeypatch.setattr(llm, "get_token_counter", lambda: adapter.counter)
        result = chat_case.send(
            MessageRequest(
                request_id=uuid4(),
                expected_thread_revision=1,
                user_input=question,
            )
        )
    assert result.turn.state == "neutral"
    assert result.turn.answer is None
    assert result.turn.sources == ()
    assert calls == []
    assert "PRIVATE_HISTORY_MARKER" not in result.model_dump_json()
    assert "PRIVATE_HISTORY_MARKER" not in caplog.text
