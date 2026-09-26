from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import text

from rag_access_guard_api.adapters import llm
from rag_access_guard_api.schemas.access import GrantView
from rag_access_guard_api.schemas.chat import MessageRequest, MessageResponse
from tests.support.chat import ChatHttp
from tests.support.chat_retry import RetryModel
from tests.support.security_mutations import prepare_mutation
from tests.support.security_races import RaceHarness

CHANGES = (
    "direct",
    "membership",
    "membership_with_direct",
    "inactive_user",
    "logout",
    "inactive_document",
    "active_version",
)


@pytest.mark.parametrize("change", CHANGES)
def test_release_waits_for_each_security_mutation(
    race_case: RaceHarness,
    role_grant: GrantView,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    change: str,
) -> None:
    case = race_case.case
    mutate = prepare_mutation(case, change, role_grant)
    model = RetryModel()
    monkeypatch.setattr(llm, "get_llm_adapter", lambda: model)
    request = MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="QUESTION")
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(
            case.client.post,
            f"/api/chat/threads/{case.thread}/messages",
            json=request.model_dump(mode="json"),
            headers=ChatHttp.csrf(case.client),
        )
        try:
            assert model.entered[0].wait(10)
            race_case.hold_mutation = True
            mutation = pool.submit(mutate)
            assert race_case.mutation_locked.wait(10)
            assert race_case.mutation_pid is not None
            for event in model.resume:
                event.set()
            _ = race_case.wait_for_blocked(race_case.mutation_pid)
        finally:
            race_case.continue_mutation.set()
            for event in model.resume:
                event.set()
        mutation.result(10)
        response = pending.result(10)
    allowed = change in {"membership_with_direct", "active_version"}
    assert len(model.inputs) == (2 if allowed else 1)
    assert response.status_code == (401 if change in {"inactive_user", "logout"} else 200)
    assert model.answers[0] not in response.text
    assert model.answers[0] not in caplog.text
    assert "PROTECTED_SYNTHETIC" not in caplog.text
    if response.status_code == 200:
        result = MessageResponse.model_validate_json(response.content)
        assert result.turn.state == ("available" if allowed else "neutral")
        assert result.turn.answer == (model.answers[1] if allowed else None)
    if change == "active_version":
        assert "PROTECTED_SYNTHETIC" not in model.inputs[1][1]
        assert "NEW_VERSION_MARKER" in model.inputs[1][1]
    with case.database.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM chat_turns WHERE answer=:discarded"),
                {"discarded": model.answers[0]},
            ).scalar_one()
            == 0
        )


@pytest.mark.parametrize("surface", ["history", "source"])
@pytest.mark.parametrize("change", CHANGES)
def test_stored_read_waits_for_each_security_mutation(
    race_case: RaceHarness,
    role_grant: GrantView,
    change: str,
    surface: str,
) -> None:
    case = race_case.case
    mutate = prepare_mutation(case, change, role_grant)
    saved = case.send(
        MessageRequest(request_id=uuid4(), expected_thread_revision=0, user_input="QUESTION")
    )
    assert saved.turn.state == "available"
    path = f"/api/chat/threads/{case.thread}" if surface == "history" else saved.turn.sources[0].url
    marker = case.model.body if surface == "history" else "PROTECTED_SYNTHETIC"
    race_case.hold_mutation = True
    with ThreadPoolExecutor(max_workers=2) as pool:
        mutation = pool.submit(mutate)
        try:
            assert race_case.mutation_locked.wait(10)
            assert race_case.mutation_pid is not None
            reading = pool.submit(case.client.get, path)
            _ = race_case.wait_for_blocked(race_case.mutation_pid)
        finally:
            race_case.continue_mutation.set()
        mutation.result(10)
        response = reading.result(10)
    allowed = change == "membership_with_direct"
    assert (marker in response.text) is allowed
    assert response.headers["cache-control"] == "private, no-store"
    if not allowed:
        assert case.document.title not in response.text
    if surface == "source":
        assert response.status_code == (200 if allowed else 404)
    else:
        assert response.status_code == (401 if change in {"inactive_user", "logout"} else 200)
