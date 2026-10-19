from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from rag_access_guard_api.adapters import llm
from rag_access_guard_api.schemas.chat import MessageRequest
from rag_access_guard_api.schemas.chat_progress import ProgressStage
from rag_access_guard_api.services.chat_progress import RequestProgressTracker
from tests.integration.test_chat_retry import (
    retry_case as retry_case,  # noqa: PLC0414 -- pytest fixture
)
from tests.support.chat import ChatHttp
from tests.support.chat_generation import ChatCase
from tests.support.chat_retry import RetryCase


@dataclass(frozen=True, slots=True)
class ProgressLog:
    stages: list[ProgressStage] = field(default_factory=list)
    entries: list[tuple[RequestProgressTracker, UUID, UUID]] = field(default_factory=list)

    def assert_clean(self) -> None:
        assert all(
            tracker.get_stage(thread, request) is None for tracker, thread, request in self.entries
        )


@pytest.fixture
def progress_log(monkeypatch: pytest.MonkeyPatch) -> Iterator[ProgressLog]:
    log = ProgressLog()
    original = RequestProgressTracker.set_stage

    def observe(
        self: RequestProgressTracker, thread_id: UUID, request_id: UUID, stage: ProgressStage
    ) -> None:
        log.stages.append(stage)
        log.entries.append((self, thread_id, request_id))
        original(self, thread_id, request_id, stage)

    monkeypatch.setattr(RequestProgressTracker, "set_stage", observe)
    yield log
    log.assert_clean()


def test_success_publishes_pipeline_order_and_cleans_up(
    chat_case: ChatCase, progress_log: ProgressLog
) -> None:
    # Given: a request with available authorized context.
    payload = MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="QUESTION")
    # When: the existing generation pipeline runs to completion.
    result = chat_case.send(payload)
    # Then: each stage is an actual pipeline boundary, with no terminal memory entry.
    assert result.turn.state == "available"
    assert progress_log.stages == ["retrieval", "generation", "authorization"]
    progress_log.assert_clean()


def test_stale_retry_returns_to_retrieval_and_cleans_up(
    retry_case: RetryCase, progress_log: ProgressLog
) -> None:
    # Given: policy changes during the first model call.
    # When: the existing bounded stale retry rebuilds context.
    result = retry_case.run_with_revocations(count=1)
    # Then: the old draft does not skip fresh retrieval or release authorization.
    assert result.turn.state == "available"
    assert progress_log.stages == [
        "retrieval",
        "generation",
        "authorization",
        "retrieval",
        "generation",
        "authorization",
    ]
    progress_log.assert_clean()


def test_no_context_skips_generation_progress(
    chat_case: ChatCase, progress_log: ProgressLog
) -> None:
    # Given: no source is authorized for this otherwise valid request.
    chat_case.revoke()
    # When: the server produces its neutral response.
    result = chat_case.send(
        MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="QUESTION")
    )
    # Then: no model-generation stage is invented.
    assert result.turn.state == "neutral"
    assert chat_case.model.call_count == 0
    assert progress_log.stages == ["retrieval", "authorization"]


def test_help_and_replay_have_no_progress_entries(
    chat_case: ChatCase, progress_log: ProgressLog
) -> None:
    # Given: the exact server-help input.
    payload = MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="помощь")
    # When: the server serves help and replays the same accepted request.
    first = chat_case.send(payload)
    second = chat_case.send(payload)
    # Then: these noncomputing responses do not publish pipeline observations.
    assert first.turn.state == "neutral"
    assert second.replayed
    assert progress_log.stages == []


def test_preadmission_unavailable_has_no_progress_or_reservation(
    chat_case: ChatCase, progress_log: ProgressLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: inference cannot admit a fresh request.
    monkeypatch.setattr(llm, "get_token_counter", lambda: None)
    # When: the request is rejected before reservation.
    response = chat_case.client.post(
        f"/api/chat/threads/{chat_case.thread}/messages",
        json=MessageRequest(
            request_id=uuid4(), expected_thread_revision=0, user_input="QUESTION"
        ).model_dump(mode="json"),
        headers=ChatHttp.csrf(chat_case.client),
    )
    # Then: progress cannot invent an admitted operation.
    assert response.status_code == 503
    assert progress_log.stages == []
    with chat_case.database.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM chat_turns")).scalar_one() == 0


def test_model_error_still_authorizes_neutral_and_cleans_up(
    chat_case: ChatCase, progress_log: ProgressLog
) -> None:
    # Given: the model fails after admission.
    chat_case.model.fail = True
    # When: the original neutral error completion runs.
    result = chat_case.send(
        MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="QUESTION")
    )
    # Then: cleanup cannot depend on a successful answer.
    assert result.turn.state == "neutral"
    assert progress_log.stages == ["retrieval", "generation", "authorization"]


def test_session_revocation_during_model_cleans_up(
    chat_case: ChatCase, progress_log: ProgressLog
) -> None:
    # Given: a model call is held without a policy lock.
    chat_case.model.hold = True
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(
            chat_case.client.post,
            f"/api/chat/threads/{chat_case.thread}/messages",
            json=MessageRequest(
                request_id=uuid4(), expected_thread_revision=0, user_input="QUESTION"
            ).model_dump(mode="json"),
            headers=ChatHttp.csrf(chat_case.client),
        )
        try:
            assert chat_case.model.entered.wait(10)
            # When: logout commits before release.
            response = chat_case.client.post(
                "/api/auth/logout", headers=ChatHttp.csrf(chat_case.client)
            )
            assert response.status_code == 204
        finally:
            chat_case.model.resume.set()
        response = running.result(10)
    # Then: the existing session denial is preserved and progress is cleared.
    assert response.status_code == 401
    assert "SYNTHETIC_ANSWER" not in response.text
    assert progress_log.stages == ["retrieval", "generation", "authorization"]
    progress_log.assert_clean()
