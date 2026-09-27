import pytest

from rag_access_guard_api.schemas.chat import ThreadDetail
from tests.support.stored_chat import StoredChatCase
from tests.support.stored_faults import ProvenanceFault, corrupt_sources


@pytest.mark.parametrize("fault", ["incomplete", "empty", "partial", "witness", "orphan", "mixed"])
@pytest.mark.parametrize("surface", ["history", "replay"])
def test_broken_saved_provenance_never_releases_protected_fields(
    stored_chat_case: StoredChatCase, fault: ProvenanceFault, surface: str
) -> None:
    corrupt_sources(stored_chat_case, fault)
    before = stored_chat_case.persisted()
    if surface == "history":
        response = stored_chat_case.read()
        assert response.status_code == 200
        turns = ThreadDetail.model_validate_json(response.content).turns
        hidden = turns[1]
        assert all(turns[index].state == "available" for index in (0, 2, 3))
    else:
        replay = stored_chat_case.case.send(stored_chat_case.requests[1])
        assert replay.replayed
        hidden = replay.turn
    assert hidden.state == "unavailable"
    assert hidden.answer is None
    assert hidden.sources == ()
    assert hidden.user_input == "QUESTION_B"
    assert stored_chat_case.persisted() == before
    assert stored_chat_case.case.model.call_count == 4
