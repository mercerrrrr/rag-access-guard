from concurrent.futures import ThreadPoolExecutor

import pytest

from rag_access_guard_api.schemas.chat import ThreadDetail
from tests.support.security_races import RaceHarness
from tests.support.stored_chat import StoredChatCase


@pytest.mark.parametrize("first", ["mutation", "read"])
def test_mixed_history_uses_one_policy_snapshot_across_all_answers(
    stored_chat_case: StoredChatCase, monkeypatch: pytest.MonkeyPatch, first: str
) -> None:
    race = RaceHarness(stored_chat_case.case)
    race.install(monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        try:
            if first == "mutation":
                race.hold_mutation = True
                mutation = pool.submit(stored_chat_case.case.revoke)
                assert race.mutation_locked.wait(10)
                assert race.mutation_pid is not None
                reading = pool.submit(stored_chat_case.read)
                _ = race.wait_for_blocked(race.mutation_pid)
            else:
                race.hold_gate = True
                reading = pool.submit(stored_chat_case.read)
                assert race.gate_locked.wait(10)
                assert race.gate_pid is not None
                mutation = pool.submit(stored_chat_case.case.revoke)
                _ = race.wait_for_blocked(race.gate_pid)
        finally:
            race.continue_gate.set()
            race.continue_mutation.set()
        response = reading.result(10)
        mutation.result(10)
    assert response.status_code == 200
    turns = ThreadDetail.model_validate_json(response.content).turns
    assert tuple(t.state for t in turns[:3]) == (
        ("unavailable",) * 3 if first == "mutation" else ("available",) * 3
    )
    assert turns[3].state == "available"
    assert len(race.waits) == 1
    assert set(race.isolation) == {"read committed"}
    assert response.headers["cache-control"] == "private, no-store"
