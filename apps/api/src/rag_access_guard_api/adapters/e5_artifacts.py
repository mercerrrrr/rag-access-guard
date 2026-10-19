"""Verify local E5 bytes independently of loading weights or running inference."""

from hashlib import file_digest
from pathlib import Path
from typing import Final

from rag_access_guard_api.adapters.tokenizer import TOKENIZER_SHA256, E5TokenCounter
from rag_access_guard_api.schemas.embedding_vectors import EmbeddingError

E5_ARTIFACTS: Final = (
    ("config.json", "69137736cab8b8903a07fe8afaafdda25aac55415a12a55d1bffa9f581abf959"),
    ("model.safetensors", "1a55775f53449dac10a2bcbc312469fac40b96d53198c407081a831f81c98477"),
    ("tokenizer.json", TOKENIZER_SHA256),
)


def verify_e5_files(path: Path) -> None:
    """Recheck pinned bytes without allocating model or tokenizer state."""
    for name, expected in E5_ARTIFACTS:
        with (path / name).open("rb") as source:
            if file_digest(source, "sha256").hexdigest() != expected:
                raise EmbeddingError


def verify_e5_artifacts(path: Path) -> E5TokenCounter:
    """Construct the tokenizer only when an inference worker needs its encoding."""
    verify_e5_files(path)
    return E5TokenCounter(path / "tokenizer.json")
