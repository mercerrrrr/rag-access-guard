import pytest

from rag_access_guard_api.adapters import llm


@pytest.mark.anyio
async def test_fake_does_not_retain_hidden_conversation() -> None:
    adapter = llm.FakeLLMAdapter()
    first = await adapter.generate(user_input="USER_PASTE", system_supplied_context="CONTEXT")
    second = await adapter.generate(user_input="NEXT", system_supplied_context="DIFFERENT")
    assert first == second == "SYNTHETIC_ANSWER"
    assert adapter.call_count == 2


def test_fake_counter_counts_words_and_punctuation_without_estimates() -> None:
    assert llm.FakeTokenCounter().count('Привет, мир! "Q"') == 7
