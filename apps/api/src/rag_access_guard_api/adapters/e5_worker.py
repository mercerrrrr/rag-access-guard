"""Compute one verified CPU model stage, then release native allocations by exiting."""

import sys
from typing import Annotated, Final, assert_never

from pydantic import Field, TypeAdapter

from rag_access_guard_api.schemas.embedding_vectors import EmbeddingError
from rag_access_guard_api.schemas.embedding_work import EmbeddingWorkRequest, EmbeddingWorkResponse
from rag_access_guard_api.schemas.generation import GenerationUnavailable
from rag_access_guard_api.schemas.relevance_work import RelevanceWorkRequest, RelevanceWorkResponse

MAX_REQUEST_BYTES: Final = 32 * 1024**2
type ModelWork = Annotated[EmbeddingWorkRequest | RelevanceWorkRequest, Field(discriminator="kind")]


def main() -> int:
    """Read private stdin and emit one bounded nonce-bound response."""
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            return 1
        request = TypeAdapter[ModelWork](ModelWork).validate_json(raw)
        match request:
            case EmbeddingWorkRequest():
                from rag_access_guard_api.adapters.e5_runtime import E5Runtime  # noqa: PLC0415

                vectors = E5Runtime(request.path).encode(request.texts, request.kind)
                response = EmbeddingWorkResponse(nonce=request.nonce, vectors=vectors)
            case RelevanceWorkRequest():
                from rag_access_guard_api.adapters.relevance_runtime import (  # noqa: PLC0415
                    score_local,
                )

                scores = score_local(request.config_path, request.question, request.texts)
                response = RelevanceWorkResponse(nonce=request.nonce, scores=scores)
            case _:
                assert_never(request)
        _ = sys.stdout.buffer.write(response.model_dump_json().encode())
    except (EmbeddingError, GenerationUnavailable, OSError, RuntimeError, ValueError, ImportError):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
