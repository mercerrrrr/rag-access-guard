"""External-only run directories and atomic snapshots of private evidence."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from pathlib import Path


def create_run_directory(output: Path, repository: Path) -> Path:
    """Reserve a new private directory without overwriting an earlier run."""
    if not output.is_absolute():
        message = "Run output must be absolute"
        raise ValueError(message)
    resolved = output.resolve()
    if resolved.is_relative_to(repository.resolve()):
        message = "Run output must be outside the public repository"
        raise ValueError(message)
    resolved.mkdir(parents=True, exist_ok=False)
    return resolved


def atomic_write(path: Path, content: bytes) -> None:
    """Replace one complete artifact without exposing a partially written record."""
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            _ = stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        _ = temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
