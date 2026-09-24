from hashlib import sha256
from itertools import pairwise
from pathlib import Path

import pytest

from rag_access_guard_api.adapters.tokenizer import (
    E5TokenCounter,
    TokenizerUnavailableError,
    get_tokenizer,
)
from rag_access_guard_api.services.chunking import chunk_text
from rag_access_guard_api.services.text_documents import DocumentError


def test_chunks_are_exact_canonical_substrings_with_overlap() -> None:
    tokenizer = get_tokenizer()
    text = (
        "  \n" + "Документы доступны сотрудникам. English document access 🧪 e\u0301.\n" * 80 + "  "
    )
    assert tokenizer.count(text) > 850
    chunks = chunk_text(text, tokenizer)
    assert len(chunks) >= 3
    covered = 0
    for ordinal, chunk in enumerate(chunks):
        assert chunk.ordinal == ordinal
        assert chunk.char_start <= covered
        assert chunk.text == text[chunk.char_start : chunk.char_end]
        assert chunk.content_sha256 == sha256(chunk.text.encode()).hexdigest()
        assert chunk.token_count == tokenizer.count(chunk.text)
        assert 0 < chunk.token_count <= 400
        assert tokenizer.embedding_input_tokens(chunk.text, kind="passage") <= 512
        covered = chunk.char_end
    assert chunks[0].char_start == 0
    assert covered == len(text)


@pytest.mark.parametrize("size", [400, 401, 1100])
def test_token_boundaries_and_overlap_preserve_tail(size: int) -> None:
    tokenizer = get_tokenizer()
    text = " ".join(["test"] * size)
    assert tokenizer.count(text) == size
    chunks = chunk_text(text, tokenizer)
    assert len(chunks) == (1 if size == 400 else 2 if size == 401 else 3)
    assert chunks[0].token_count == 400
    assert chunks[-1].char_end == len(text)
    for left, right in pairwise(chunks):
        assert tokenizer.count(text[right.char_start : left.char_end]) == 50
    assert chunk_text(text, tokenizer) == chunks


@pytest.mark.parametrize("fragment", ["🧪", "e\u0301", "\u200b", "ﬃ", "中文", "<s>"])
def test_unicode_offsets_do_not_drop_non_whitespace(fragment: str) -> None:
    tokenizer = get_tokenizer()
    text = " \n" + ("hello " + fragment + " ") * 420 + "\n "
    chunks = chunk_text(text, tokenizer)
    assert chunks[0].char_start == 0
    assert chunks[-1].char_end == len(text)
    assert all(left.char_end >= right.char_start for left, right in pairwise(chunks))
    assert all(chunk.text == text[chunk.char_start : chunk.char_end] for chunk in chunks)


@pytest.mark.parametrize("text", ["", " \t\n"])
def test_blank_text_fails(text: str) -> None:
    with pytest.raises(DocumentError):
        _ = chunk_text(text, get_tokenizer())


def test_tokenizer_rejects_unpinned_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "tokenizer.json"
    _ = artifact.write_bytes(b'{"model":"untrusted"}')
    with pytest.raises(TokenizerUnavailableError):
        _ = E5TokenCounter(artifact)


@pytest.mark.parametrize("offsets", [(), ((0, 0),), ((-1, 2),), ((0, 9),), ((1, 2), (0, 1))])
def test_unsafe_offsets_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    offsets: tuple[tuple[int, int], ...],
) -> None:
    def broken_offsets(_self: E5TokenCounter, _text: str) -> tuple[tuple[int, int], ...]:
        return offsets

    monkeypatch.setattr(E5TokenCounter, "offsets", broken_offsets)
    with pytest.raises(DocumentError) as failure:
        _ = chunk_text("test", get_tokenizer())
    assert failure.value.code == "parse_failed"
