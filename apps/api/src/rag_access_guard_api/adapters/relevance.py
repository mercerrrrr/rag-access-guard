"""Bounded CPU relevance scoring before one stateless generation request."""

import math
import sys
from functools import partial
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from rag_access_guard_api.adapters.inference_runtime import current_runtime
from rag_access_guard_api.schemas.generation import GenerationUnavailable
from rag_access_guard_api.schemas.relevance_config import (
    INPUT_BYTE_LIMIT,
    PASSAGE_LIMIT,
    REJECT_BELOW,
    STRICT_BELOW,
)
from rag_access_guard_api.services.evidence_context import EvidencePassage, evidence_passages

type EvidenceRoute = Literal["none", "strict", "sufficient"]


def classify_relevance(scores: tuple[float, ...]) -> EvidenceRoute:
    """Treat low relevance as uncertain evidence, never as an authorization grant."""
    if any(not math.isfinite(score) for score in scores):
        raise GenerationUnavailable
    if not scores or max(scores) < REJECT_BELOW:
        return "none"
    if max(scores) < STRICT_BELOW:
        return "strict"
    return "sufficient"


def _score(path: Path, question: str, entries: tuple[EvidencePassage, ...]) -> tuple[float, ...]:
    """Keep all native relevance allocations outside the Windows API process."""
    texts = tuple(entry.text for entry in entries)
    if sys.platform != "win32":
        from rag_access_guard_api.adapters.relevance_runtime import score_local  # noqa: PLC0415

        return score_local(path, question, texts)
    from rag_access_guard_api.adapters.e5_process import (  # noqa: PLC0415
        EmbeddingProcessUncertainError,
        score_relevance_isolated,
    )
    from rag_access_guard_api.schemas.embedding_vectors import EmbeddingError  # noqa: PLC0415

    runtime = current_runtime()
    try:
        return score_relevance_isolated(path, question, texts, runtime)
    except EmbeddingProcessUncertainError:
        runtime.close()
        raise GenerationUnavailable from None
    except EmbeddingError:
        raise GenerationUnavailable from None


async def assess_relevance(
    path: Path, *, user_input: str, system_supplied_context: str
) -> EvidenceRoute:
    """Score only prepared managed evidence inside the existing physical-future admission."""
    try:
        entries = evidence_passages(system_supplied_context)
        if not entries:
            return "none"
        if (
            len(entries) > PASSAGE_LIMIT
            or len(user_input.encode()) + sum(len(entry.text.encode()) for entry in entries)
            > INPUT_BYTE_LIMIT
        ):
            raise GenerationUnavailable
        scores = await current_runtime().run_embedding(partial(_score, path, user_input, entries))
        if len(scores) != len(entries):
            raise GenerationUnavailable
        return classify_relevance(scores)
    except (OSError, ValueError, ValidationError, ImportError, MemoryError):
        raise GenerationUnavailable from None
