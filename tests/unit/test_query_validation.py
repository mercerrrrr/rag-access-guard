from dataclasses import dataclass
from typing import Literal

import pytest

from rag_access_guard_api.adapters.tokenizer import TokenizerUnavailableError, get_tokenizer
from rag_access_guard_api.schemas.search import InvalidSearchError, SearchError
from rag_access_guard_api.services import query_validation


@dataclass(frozen=True, slots=True)
class QueryCounter:
    total: int

    def embedding_input_tokens(self, text: str, *, kind: Literal["query", "passage"]) -> int:
        assert text == "Вопрос 🙂"
        assert kind == "query"
        return self.total


@dataclass(frozen=True, slots=True)
class ModelCounter:
    total: int
    identity: str = "deterministic-query-boundary"

    def count(self, text: str) -> int:
        assert text == "Вопрос 🙂"
        return self.total


@pytest.mark.parametrize(("e5_tokens", "model_tokens"), [(512, 1024), (511, 1023)])
def test_both_exact_token_limits_are_accepted(
    monkeypatch: pytest.MonkeyPatch, e5_tokens: int, model_tokens: int
) -> None:
    monkeypatch.setattr(query_validation, "get_tokenizer", lambda: QueryCounter(e5_tokens))
    query_validation.validate_query("Вопрос 🙂", model_counter=ModelCounter(model_tokens))


@pytest.mark.parametrize(("e5_tokens", "model_tokens"), [(513, 1024), (512, 1025)])
def test_either_token_limit_overflow_is_rejected(
    monkeypatch: pytest.MonkeyPatch, e5_tokens: int, model_tokens: int
) -> None:
    monkeypatch.setattr(query_validation, "get_tokenizer", lambda: QueryCounter(e5_tokens))
    with pytest.raises(query_validation.QueryTooLongError):
        query_validation.validate_query("Вопрос 🙂", model_counter=ModelCounter(model_tokens))


@pytest.mark.parametrize(
    "value",
    ["", " \n\t", "\x00", "я" * 8193, "🙂" * 4097],
    ids=["empty", "blank", "nul", "cyrillic-bytes", "emoji-bytes"],
)
def test_invalid_text_is_rejected_without_loading_tokenizer(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    def unexpected() -> QueryCounter:
        pytest.fail("Invalid text must not initialize a tokenizer")

    monkeypatch.setattr(query_validation, "get_tokenizer", unexpected)
    with pytest.raises(InvalidSearchError):
        query_validation.validate_query(value, model_counter=None)


@pytest.mark.parametrize(("word_count", "total"), [(507, 512), (508, 513)])
def test_pinned_e5_budget_includes_prefix_and_special_tokens(word_count: int, total: int) -> None:
    query = " ".join(["test"] * word_count)
    assert get_tokenizer().count(query) == word_count
    assert get_tokenizer().embedding_input_tokens(query, kind="query") == total
    if total == 512:
        query_validation.validate_query(query, model_counter=None)
    else:
        with pytest.raises(query_validation.QueryTooLongError):
            query_validation.validate_query(query, model_counter=None)


@pytest.mark.parametrize("value", ["Вопрос по документам", "🙂", "  Вопрос\n  "])
def test_pinned_tokenizer_accepts_multilingual_text_without_normalization(value: str) -> None:
    query_validation.validate_query(value, model_counter=None)


def test_unavailable_tokenizer_propagates_without_input_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable() -> QueryCounter:
        raise TokenizerUnavailableError

    monkeypatch.setattr(query_validation, "get_tokenizer", unavailable)
    with pytest.raises(SearchError) as raised:
        query_validation.validate_query("Вопрос 🙂", model_counter=None)
    assert isinstance(raised.value.__cause__, TokenizerUnavailableError)
