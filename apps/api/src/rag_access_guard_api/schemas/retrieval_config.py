"""Integrity-bound calibration for one pinned retrieval recipe."""

import json
import math
from hashlib import sha256
from pathlib import Path
from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from rag_access_guard_api.adapters.embeddings import MODEL_ID
from rag_access_guard_api.adapters.tokenizer import MODEL_REVISION, TOKENIZER_IDENTITY
from rag_access_guard_api.schemas.search import RetrievalNotConfiguredError
from rag_access_guard_api.services.chunking import CHUNKER_REVISION

type Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class RetrievalConfig(BaseModel):
    """Threshold and dataset identity are hashed with the complete recipe."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True, strict=True)
    threshold: float
    no_results: bool
    dataset_sha256: Digest
    corpus_sha256: Digest
    model_id: str
    model_revision: str
    tokenizer_revision: str
    chunker_revision: str
    scorer_revision: Literal["micro-f1-top5-v1"]
    similarity_revision: Literal["pgvector-0.8.6-vector-cosine-v1"]
    config_sha256: Digest

    def computed_hash(self) -> str:
        """Hash canonical JSON excluding only the self-referential digest."""
        payload = json.dumps(
            self.model_dump(exclude={"config_sha256"}), sort_keys=True, separators=(",", ":")
        )
        return sha256(payload.encode("utf-8")).hexdigest()

    def require_compatible(self) -> None:
        """Reject altered config, unsupported recipes and implicit sentinels."""
        threshold_valid = (
            self.threshold == math.nextafter(1.0, math.inf)
            if self.no_results
            else -1.0 <= self.threshold <= 1.0
        )
        if (
            not math.isfinite(self.threshold)
            or not threshold_valid
            or self.model_id != MODEL_ID
            or self.model_revision != MODEL_REVISION
            or self.tokenizer_revision != TOKENIZER_IDENTITY
            or self.chunker_revision != CHUNKER_REVISION
            or self.config_sha256 != self.computed_hash()
        ):
            raise RetrievalNotConfiguredError


def load_retrieval_config(path: Path | None) -> RetrievalConfig:
    """Missing or malformed calibration never falls back to a guessed threshold."""
    if path is None:
        raise RetrievalNotConfiguredError
    try:
        config = RetrievalConfig.model_validate_json(path.read_bytes())
        config.require_compatible()
    except (OSError, ValidationError) as error:
        raise RetrievalNotConfiguredError from error
    return config
