"""Exact query budgets shared by new chat generation and document search."""

from rag_access_guard import TokenCounter
from rag_access_guard_api.adapters.tokenizer import TokenizerUnavailableError, get_tokenizer
from rag_access_guard_api.schemas.chat import MAX_INPUT_BYTES, MAX_INPUT_TOKENS
from rag_access_guard_api.schemas.search import InvalidSearchError, SearchError
from rag_access_guard_api.services.chunking import EMBEDDING_LIMIT


class QueryTooLongError(InvalidSearchError):
    """A new query exceeds an exact tokenizer budget, without input details."""


def validate_query(text: str, *, model_counter: TokenCounter | None) -> None:
    """Reject full prefixed E5 input and optional generator input without truncation."""
    if not text.strip() or "\x00" in text or len(text.encode("utf-8")) > MAX_INPUT_BYTES:
        raise InvalidSearchError
    try:
        query_tokens = get_tokenizer().embedding_input_tokens(text, kind="query")
    except (OSError, TokenizerUnavailableError) as error:
        raise SearchError from error
    if query_tokens > EMBEDDING_LIMIT:
        raise QueryTooLongError
    if model_counter is not None and model_counter.count(text) > MAX_INPUT_TOKENS:
        raise QueryTooLongError
