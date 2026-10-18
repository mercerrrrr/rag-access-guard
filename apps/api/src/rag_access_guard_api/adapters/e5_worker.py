"""Compute one verified E5 batch, then release all native allocations by exiting."""

import sys
from typing import Final

from pydantic import ValidationError

from rag_access_guard_api.schemas.embedding_vectors import EmbeddingError
from rag_access_guard_api.schemas.embedding_work import EmbeddingWorkRequest, EmbeddingWorkResponse

MAX_REQUEST_BYTES: Final = 32 * 1024**2


def main() -> int:
    """Read private stdin and emit one bounded nonce-bound response."""
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            return 1
        request = EmbeddingWorkRequest.model_validate_json(raw)
        from rag_access_guard_api.adapters.e5_runtime import E5Runtime  # noqa: PLC0415

        vectors = E5Runtime(request.path).encode(request.texts, request.kind)
        response = EmbeddingWorkResponse(nonce=request.nonce, vectors=vectors)
        _ = sys.stdout.buffer.write(response.model_dump_json().encode())
    except (EmbeddingError, OSError, RuntimeError, ValueError, ValidationError):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
