"""Verified safetensors loading and mask-aware E5 inference on CPU."""

from functools import cache
from hashlib import file_digest
from pathlib import Path
from typing import Final, Literal

import torch
from torch import Tensor
from transformers import BertModel

from rag_access_guard_api.adapters.tokenizer import E5TokenCounter
from rag_access_guard_api.schemas.embedding_vectors import EmbeddingError, validate_vectors

INPUT_LIMIT: Final = 512


def average_pool(hidden: Tensor, mask: Tensor) -> Tensor:
    """Exclude padding from both the hidden-state sum and its divisor."""
    return hidden.masked_fill(~mask[..., None].bool(), 0.0).sum(dim=1) / mask.sum(dim=1)[..., None]


class E5Runtime:
    """Own one verified, evaluation-only model; its caller serializes CPU work."""

    def __init__(self, path: Path) -> None:
        """Read only pinned local artifacts, never pickle or remote executable code."""
        artifacts = (
            ("config.json", "69137736cab8b8903a07fe8afaafdda25aac55415a12a55d1bffa9f581abf959"),
            (
                "model.safetensors",
                "1a55775f53449dac10a2bcbc312469fac40b96d53198c407081a831f81c98477",
            ),
        )
        for name, expected in artifacts:
            with (path / name).open("rb") as source:
                if file_digest(source, "sha256").hexdigest() != expected:
                    raise EmbeddingError
        self.tokenizer: E5TokenCounter = E5TokenCounter(path / "tokenizer.json")
        torch.set_num_threads(2)
        self.model: BertModel = BertModel.from_pretrained(
            str(path), local_files_only=True, use_safetensors=True, attn_implementation="eager"
        )
        _ = self.model.eval()

    def encode(
        self, texts: tuple[str, ...], kind: Literal["query", "passage"]
    ) -> tuple[tuple[float, ...], ...]:
        """Reject oversized inputs before inference; never truncate source tokens."""
        inputs = tuple(self.tokenizer.embedding_input_ids(text, kind=kind) for text in texts)
        if (
            not inputs
            or any(not text.strip() for text in texts)
            or any(len(ids) > INPUT_LIMIT for ids in inputs)
        ):
            raise EmbeddingError
        vectors: list[tuple[float, ...]] = []
        with torch.inference_mode():
            for start in range(0, len(inputs), 16):
                batch = inputs[start : start + 16]
                width = max(map(len, batch))
                ids = torch.tensor([(*row, *(1 for _ in range(width - len(row)))) for row in batch])
                mask = torch.tensor(
                    [(*(1 for _ in row), *(0 for _ in range(width - len(row)))) for row in batch]
                )
                hidden = self.model(input_ids=ids, attention_mask=mask).last_hidden_state
                pooled = average_pool(hidden, mask)
                normalized = torch.nn.functional.normalize(pooled, p=2, dim=1)
                vectors.extend(
                    tuple(float(value.item()) for value in row.unbind())
                    for row in normalized.unbind()
                )
        result = tuple(vectors)
        validate_vectors(result, len(texts))
        return result


@cache
def get_runtime(path: Path) -> E5Runtime:
    """Construct the model only inside the adapter's worker-thread lock."""
    return E5Runtime(path)
