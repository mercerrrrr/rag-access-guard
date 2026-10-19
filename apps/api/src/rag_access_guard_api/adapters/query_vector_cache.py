"""Bound pure E5 query computation to an application's weak runtime lifetime."""

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Final
from weakref import WeakKeyDictionary

from rag_access_guard_api.adapters.inference_runtime import InferenceRuntime

QUERY_CACHE_CAPACITY: Final = 128


@dataclass(frozen=True, slots=True)
class QueryVectorKey:
    """Identify exact input bytes and artifacts without retaining query plaintext."""

    model_id: str
    revision: str
    model_path: Path
    query_sha256: bytes


class QueryVectorCache:
    """Mutate only a bounded LRU of validated immutable vectors, never authorization."""

    def __init__(self) -> None:
        """Create a lock and bounded storage without retaining its runtime owner."""
        self._lock: Lock = Lock()
        self._vectors: OrderedDict[QueryVectorKey, tuple[float, ...]] = OrderedDict()

    def get(self, key: QueryVectorKey) -> tuple[float, ...] | None:
        """Refresh the most recently used computation under the cache lock."""
        with self._lock:
            value = self._vectors.get(key)
            if value is not None:
                self._vectors.move_to_end(key)
            return value

    def put(self, key: QueryVectorKey, vector: tuple[float, ...]) -> None:
        """Retain one already validated vector and evict the least recently used key."""
        with self._lock:
            self._vectors[key] = vector
            self._vectors.move_to_end(key)
            if len(self._vectors) > QUERY_CACHE_CAPACITY:
                _ = self._vectors.popitem(last=False)


_CACHES: Final[WeakKeyDictionary[InferenceRuntime, QueryVectorCache]] = WeakKeyDictionary()
_CACHE_LOCK: Final = Lock()


def runtime_query_cache(runtime: InferenceRuntime) -> QueryVectorCache:
    """Keep no strong runtime reference in registry values or cached computations."""
    with _CACHE_LOCK:
        cached = _CACHES.get(runtime)
        if cached is None:
            cached = QueryVectorCache()
            _CACHES[runtime] = cached
        return cached
