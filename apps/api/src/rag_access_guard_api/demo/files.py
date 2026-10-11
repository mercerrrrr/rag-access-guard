"""Bounded private file boundaries shared by configuration, journals, and reports."""

import json
import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Final

from pydantic import BaseModel, JsonValue, ValidationError

from rag_access_guard_api.demo.contracts import DemoConfig
from rag_access_guard_api.demo.errors import DemoOwnershipError
from rag_access_guard_api.demo.paths import private_path
from rag_access_guard_api.demo.private_write import owner_only
from rag_access_guard_api.services.demo_corpus import REPOSITORY_ROOT

MAX_JSON_BYTES: Final = 1_048_576


def bounded_bytes(path: Path, limit: int = MAX_JSON_BYTES) -> bytes:
    """Read a regular private file with an actual growth-aware upper bound."""
    try:
        target = private_path(path)
        with target.open("rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= limit:
                raise DemoOwnershipError(code="file_size_limit")
            data = stream.read(limit + 1)
            if not data or len(data) > limit:
                raise DemoOwnershipError(code="file_size_limit")
            return data
    except OSError:
        raise DemoOwnershipError(code="private_file_unavailable") from None


def _unique_members(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise DemoOwnershipError(code="invalid_config")
        result[key] = value
    return result


def _scan_json(decoder: Callable[..., JsonValue], data: bytes) -> None:
    _ = decoder(data, object_pairs_hook=_unique_members)


def load_private_json[T: BaseModel](path: Path, model: type[T]) -> T:
    """Convert invalid JSON/model errors to a safe code without their raw inputs."""
    try:
        data = bounded_bytes(path)
        _scan_json(json.loads, data)
        return model.model_validate_json(data, strict=True)
    except (ValidationError, json.JSONDecodeError, UnicodeError, RecursionError):
        raise DemoOwnershipError(code="invalid_config") from None


def load_config(path: Path) -> DemoConfig:
    """Parse the shared private file and reject public or aliased output paths."""
    _ = private_path(path)
    owner_only(path.parent)
    owner_only(path)
    config = load_private_json(path, DemoConfig)
    for candidate in (
        config.corpus_manifest,
        config.ownership_manifest,
        config.credentials_path,
        config.access_definition,
        config.retrieval_config,
        config.runtime.state_directory,
        config.runtime.log_directory,
        config.runtime.ollama_supervisor_config,
        config.report_path or config.ownership_manifest.parent / "seed-report.json",
        config.suggestions_path or config.ownership_manifest.parent / "suggestions.json",
    ):
        if not candidate.is_absolute():
            raise DemoOwnershipError(code="absolute_private_path_required")
        _ = private_path(candidate)
    if config.runtime.repository_root.resolve() != REPOSITORY_ROOT:
        raise DemoOwnershipError(code="repository_mismatch")
    return config
