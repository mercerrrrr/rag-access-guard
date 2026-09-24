"""Pinned E5 tokenization without network access or executable model code."""

import os
from functools import cache
from hashlib import sha256
from pathlib import Path
from typing import Final, Literal

from tokenizers import Tokenizer

MODEL_REVISION: Final = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
TOKENIZER_IDENTITY: Final = f"intfloat/multilingual-e5-small@{MODEL_REVISION}:content-no-special"
TOKENIZER_SHA256: Final = "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39"


class TokenizerUnavailableError(RuntimeError):
    """The operator must provision the pinned tokenizer before ingestion."""


class E5TokenCounter:
    """Keep one read-only native tokenizer for counting and source offsets."""

    def __init__(self, path: Path) -> None:
        """Verify the pinned artifact before constructing the native tokenizer."""
        if sha256(path.read_bytes()).hexdigest() != TOKENIZER_SHA256:
            raise TokenizerUnavailableError
        self._tokenizer: Tokenizer = Tokenizer.from_file(str(path))
        self._tokenizer.no_padding()
        self._tokenizer.no_truncation()

    @property
    def identity(self) -> str:
        """Identify the content counter, independently of embedding prefixes."""
        return TOKENIZER_IDENTITY

    def count(self, text: str) -> int:
        """Count content tokens without truncation, prefixes or special tokens."""
        return len(self._tokenizer.encode(text, add_special_tokens=False).ids)

    def offsets(self, text: str) -> tuple[tuple[int, int], ...]:
        """Return character offsets into the original string, not decoded tokens."""
        return tuple(self._tokenizer.encode(text, add_special_tokens=False).offsets)

    def embedding_input_tokens(self, text: str, *, kind: Literal["query", "passage"]) -> int:
        """Count the complete input; callers reject over-budget inputs explicitly."""
        return len(self._tokenizer.encode(f"{kind}: {text}", add_special_tokens=True).ids)


@cache
def get_tokenizer() -> E5TokenCounter:
    """Load only an operator-provisioned local artifact, once per process."""
    path = Path(
        os.environ.get(
            "RAG_ACCESS_GUARD_TOKENIZER_PATH", f".cache/e5/{MODEL_REVISION}/tokenizer.json"
        )
    )
    return E5TokenCounter(path)
