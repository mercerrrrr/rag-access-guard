"""Local embedding boundary independent of document persistence."""

import gc
import os
import sys
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from threading import Lock
from typing import Final, Literal, Protocol, final, override

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
    retain_model: bool = True

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

                if self.retain_model:
                    return get_runtime(self.path).encode(texts, kind)
                result: tuple[tuple[float, ...], ...] | None = None
                try:
                    result = get_runtime(self.path).encode(texts, kind)
                except (EmbeddingError, OSError, RuntimeError, ValueError):
                    result = None
                finally:
                    get_runtime.cache_clear()
                    _ = gc.collect()
            except (OSError, RuntimeError, ValueError) as error:
                raise EmbeddingError from error
            else:
                if result is None:
                    raise EmbeddingError
                return result


@final
class IsolatedE5EmbeddingAdapter(E5EmbeddingAdapter):
    """Keep the physical Future without loading PyTorch into the API process."""

    __slots__ = ()

    @override
    def _encode(
        self, texts: tuple[str, ...], kind: Literal["query", "passage"]
    ) -> tuple[tuple[float, ...], ...]:
        from rag_access_guard_api.adapters.e5_process import (  # noqa: PLC0415
            EmbeddingProcessUncertainError,
            encode_isolated,
        )

        with _MODEL_LOCK:
            try:
                return encode_isolated(self.path, texts, kind, self.runtime)
            except (EmbeddingProcessUncertainError, OSError):
                self.runtime.close()
                raise EmbeddingError from None


def get_embedding_adapter() -> EmbeddingAdapter:
    """Return the locally provisioned embedding model."""
    path = Path(os.environ.get("RAG_ACCESS_GUARD_MODEL_PATH", f".cache/e5/{MODEL_REVISION}"))
    extractive = os.environ.get("RAG_ACCESS_GUARD_MODEL_PROFILE") in {
        "qwen35-extractive-demo-v1",
        "qwen35-extractive-text-demo-v1",
    }
    if extractive and sys.platform == "win32" and sys.maxsize > 2**32:
        return IsolatedE5EmbeddingAdapter(path, runtime=current_runtime(), retain_model=False)
    return E5EmbeddingAdapter(
        path,
        runtime=current_runtime(),
        retain_model=not extractive,
    )
