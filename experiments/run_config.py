"""Explicit structural-run settings and a self-contained synthetic retrieval recipe."""

from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from experiments.comparison_config import ComparisonConfig
from experiments.scenario_types import FrozenModel
from rag_access_guard_api.adapters.embeddings import MODEL_ID
from rag_access_guard_api.adapters.tokenizer import MODEL_REVISION, TOKENIZER_IDENTITY
from rag_access_guard_api.schemas.retrieval_config import RetrievalConfig
from rag_access_guard_api.services.chunking import CHUNKER_REVISION


class RunConfig(FrozenModel):
    """Warmups and immutable adapter settings form part of the reproduction identity."""

    schema_version: Literal[1] = 1
    comparison: ComparisonConfig = ComparisonConfig()
    warmup_repetitions: Annotated[int, Field(ge=0, le=100)] = 0


class RunOptions(FrozenModel):
    """Validated CLI inputs; output paths remain outside the public checkout."""

    scenarios: Path
    config: Path
    seed: int
    repetitions: Annotated[int, Field(gt=0)]
    llm: Literal["fake", "ollama"]
    output: Path
    reproduce: Path | None


def synthetic_retrieval(corpus_hash: str) -> RetrievalConfig:
    """Structural vectors use a declared threshold, never a claimed E5 quality calibration."""
    config = RetrievalConfig(
        threshold=0.8,
        no_results=False,
        dataset_sha256=sha256(b"synthetic-text-hash-ranking-384-no-calibration-v2").hexdigest(),
        corpus_sha256=corpus_hash,
        model_id=MODEL_ID,
        model_revision=MODEL_REVISION,
        tokenizer_revision=TOKENIZER_IDENTITY,
        chunker_revision=CHUNKER_REVISION,
        scorer_revision="micro-f1-top5-v1",
        similarity_revision="pgvector-0.8.6-vector-cosine-v1",
        config_sha256="0" * 64,
    )
    return config.model_copy(update={"config_sha256": config.computed_hash()})
