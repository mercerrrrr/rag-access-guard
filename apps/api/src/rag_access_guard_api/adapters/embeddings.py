"""Local embedding boundary independent of document persistence."""

import gc
import os
import sys
from dataclasses import dataclass, field
from functools import partial
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Final, Literal, Protocol, final, override

from rag_access_guard_api.adapters.inference_runtime import InferenceRuntime, current_runtime
from rag_access_guard_api.adapters.query_vector_cache import QueryVectorKey, runtime_query_cache
from rag_access_guard_api.adapters.tokenizer import MODEL_REVISION
from rag_access_guard_api.schemas.embedding_vectors import (
    EMBEDDING_DIMENSION,
    EmbeddingError,
    validate_vectors,
)
from rag_access_guard_api.schemas.generation import InferenceUnavailableError

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
    cache_queries: bool = False

    async def embed_passages(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        """Embed canonical passages with the retrieval prefix."""
        return await self.runtime.run_embedding(partial(self._encode, texts, "passage"))

    async def embed_query(self, text: str) -> tuple[float, ...]:
        """Embed one query with the query prefix."""
        if self.cache_queries:
            return await self.runtime.run_embedding(partial(self._cached_query, text))
        return (await self.runtime.run_embedding(partial(self._encode, (text,), "query")))[0]

    def _cached_query(self, text: str) -> tuple[float, ...]:
        if self.runtime.generation_quarantined:
            raise InferenceUnavailableError
        key = QueryVectorKey(
            self.model_id, self.revision, self.path.resolve(), sha256(text.encode("utf-8")).digest()
        )
        cache = runtime_query_cache(self.runtime)
        cached = cache.get(key)
        if cached is not None:
            return cached
        vectors = self._encode((text,), "query")
        validate_vectors(vectors, 1)
        if self.runtime.generation_quarantined:
            raise InferenceUnavailableError
        vector = tuple(vectors[0])
        cache.put(key, vector)
        return vector

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
    profile = os.environ.get("RAG_ACCESS_GUARD_MODEL_PROFILE")
    resident_allowed = profile == "qwen35-extractive-cpu-demo-v1"
    cache_queries = resident_allowed or profile == "qwen25-extractive-small-demo-v2"
    execution = os.environ.get("RAG_ACCESS_GUARD_E5_EXECUTION", "isolated")
    if execution not in {"isolated", "resident"} or (
        execution == "resident" and not resident_allowed
    ):
        raise EmbeddingError
    if execution == "resident":
        return E5EmbeddingAdapter(
            path, runtime=current_runtime(), retain_model=True, cache_queries=True
        )
    extractive = os.environ.get("RAG_ACCESS_GUARD_MODEL_PROFILE") in {
        "qwen25-extractive-small-demo-v1",
        "qwen25-extractive-small-demo-v2",
        "qwen35-extractive-demo-v1",
        "qwen35-extractive-text-demo-v1",
        "qwen35-extractive-cpu-demo-v1",
    }
    if extractive and sys.platform == "win32" and sys.maxsize > 2**32:
        return IsolatedE5EmbeddingAdapter(
            path, runtime=current_runtime(), retain_model=False, cache_queries=cache_queries
        )
    return E5EmbeddingAdapter(
        path,
        runtime=current_runtime(),
        retain_model=not extractive,
        cache_queries=cache_queries,
    )
