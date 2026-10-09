"""Local embedding boundary independent of document persistence."""

import os
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from threading import Lock
from typing import Final, Literal, Protocol

from rag_access_guard_api.adapters.inference_runtime import InferenceRuntime, current_runtime
from rag_access_guard_api.adapters.tokenizer import MODEL_REVISION
from rag_access_guard_api.schemas.embedding_vectors import (
    EMBEDDING_DIMENSION,
    EmbeddingError,
    validate_vectors,
)

__all__ = [
    "E5EmbeddingAdapter",
    "EmbeddingAdapter",
    "EmbeddingError",
    "get_embedding_adapter",
    "validate_vectors",
]
MODEL_ID: Final = "intfloat/multilingual-e5-small"
_MODEL_LOCK: Final = Lock()


class EmbeddingAdapter(Protocol):
    """Produce normalized vectors under one immutable model identity."""

    @property
    def model_id(self) -> str:
        """Identify the model bound into each document manifest."""
        ...

    @property
    def revision(self) -> str:
        """Return the immutable model snapshot revision."""
        ...

    @property
    def dimension(self) -> int:
        """Declare the dimension checked before persistence."""
        ...

    async def embed_passages(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        """Return one passage-prefixed vector per input, preserving order."""
        ...

    async def embed_query(self, text: str) -> tuple[float, ...]:
        """Return a query-prefixed vector without truncating input."""
        ...


@dataclass(frozen=True, slots=True)
class E5EmbeddingAdapter:
    """Serialize bounded CPU inference without holding event-loop or database locks."""

    path: Path
    model_id: str = MODEL_ID
    revision: str = MODEL_REVISION
    dimension: int = EMBEDDING_DIMENSION
    runtime: InferenceRuntime = field(default_factory=current_runtime, repr=False, compare=False)

    async def embed_passages(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        """Embed canonical passages with the retrieval prefix."""
        return await self.runtime.run_embedding(partial(self._encode, texts, "passage"))

    async def embed_query(self, text: str) -> tuple[float, ...]:
        """Embed one query with the query prefix."""
        return (await self.runtime.run_embedding(partial(self._encode, (text,), "query")))[0]

    def _encode(
        self, texts: tuple[str, ...], kind: Literal["query", "passage"]
    ) -> tuple[tuple[float, ...], ...]:
        with _MODEL_LOCK:
            try:
                from rag_access_guard_api.adapters.e5_runtime import get_runtime  # noqa: PLC0415

                return get_runtime(self.path).encode(texts, kind)
            except (OSError, RuntimeError, ValueError) as error:
                raise EmbeddingError from error


def get_embedding_adapter() -> EmbeddingAdapter:
    """Return the locally provisioned embedding model."""
    return E5EmbeddingAdapter(
        Path(os.environ.get("RAG_ACCESS_GUARD_MODEL_PATH", f".cache/e5/{MODEL_REVISION}")),
        runtime=current_runtime(),
    )
