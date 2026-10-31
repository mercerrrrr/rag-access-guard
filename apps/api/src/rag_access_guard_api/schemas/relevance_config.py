"""Pinned local relevance artifacts and the explicit, versioned routing policy."""

import json
from hashlib import file_digest, sha256
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import ClassVar, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from rag_access_guard_api.schemas.generation import GenerationUnavailable

REJECT_BELOW: Final = 0.0
STRICT_BELOW: Final = 2.0
PAIR_LIMIT: Final = 512
PASSAGE_LIMIT: Final = 32
INPUT_BYTE_LIMIT: Final = 65536
CONFIG_BYTE_LIMIT: Final = 4096
RUNTIME_VERSION: Final = "1.31.1"
SESSION_THREADS: Final = 2
SESSION_INTER_THREADS: Final = 1
MODEL_SHA256: Final = "6c2513767fb63d008a4377bef7a7a3555433d9436342bb53e35a3a72ffc52d4b"
TOKENIZER_SHA256: Final = "62c24cdc13d4c9952d63718d6c9fa4c287974249e16b7ade6d5a85e7bbb75626"
ARTIFACTS: Final = (
    ("onnx/model_quint8_avx2.onnx", 118620016, MODEL_SHA256),
    ("tokenizer.json", 17082660, TOKENIZER_SHA256),
)


class RelevanceConfig(BaseModel):
    """Allow an operator path, not a remote model or client-supplied routing rules."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True, hide_input_in_errors=True
    )
    schema_version: Literal["relevance-triage-v1"] = "relevance-triage-v1"
    model_id: Literal["cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"] = (
        "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
    )
    revision: Literal["1427fd652930e4ba29e8149678df786c240d8825"] = (
        "1427fd652930e4ba29e8149678df786c240d8825"
    )
    model_path: Path

    @model_validator(mode="after")
    def absolute_model_path(self) -> Self:
        """Keep artifact resolution independent of the process working directory."""
        if not self.model_path.is_absolute():
            message = "Relevance artifacts require an absolute local path"
            raise ValueError(message)
        return self

    @staticmethod
    def computed_hash() -> str:
        """Bind semantic artifacts, runtime and thresholds while excluding host paths."""
        payload = {
            "schema_version": "relevance-triage-v1",
            "model_id": "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
            "revision": "1427fd652930e4ba29e8149678df786c240d8825",
            "artifacts": ARTIFACTS,
            "runtime": RUNTIME_VERSION,
            "reject_below": REJECT_BELOW,
            "strict_below": STRICT_BELOW,
            "pair_limit": PAIR_LIMIT,
            "passage_limit": PASSAGE_LIMIT,
            "input_byte_limit": INPUT_BYTE_LIMIT,
            "providers": ["CPUExecutionProvider"],
            "session_threads": SESSION_THREADS,
            "session_inter_threads": SESSION_INTER_THREADS,
            "cpu_mem_arena": False,
            "mem_pattern": False,
        }
        return sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


def load_relevance_config(path: Path) -> RelevanceConfig:
    """Bound configuration reads and hide local details on invalid operator input."""
    try:
        with path.open("rb") as source:
            raw = source.read(CONFIG_BYTE_LIMIT + 1)
        if len(raw) > CONFIG_BYTE_LIMIT:
            raise GenerationUnavailable
        return RelevanceConfig.model_validate_json(raw)
    except (OSError, ValidationError):
        raise GenerationUnavailable from None


def verify_relevance_artifacts(config: RelevanceConfig) -> None:
    """Read exact local bytes before loading a graph; never fetch or execute remote code."""
    try:
        for name, size, expected in ARTIFACTS:
            path = config.model_path / name
            if path.stat().st_size != size:
                raise GenerationUnavailable
            with path.open("rb") as source:
                if file_digest(source, "sha256").hexdigest() != expected:
                    raise GenerationUnavailable
        if version("onnxruntime") != RUNTIME_VERSION:
            raise GenerationUnavailable
    except (OSError, PackageNotFoundError):
        raise GenerationUnavailable from None
