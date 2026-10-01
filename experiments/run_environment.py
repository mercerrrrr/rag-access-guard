"""Local code and runtime evidence, written only to the private run manifest."""

import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import psutil
import psycopg
from psycopg.rows import class_row
from pydantic import JsonValue

from experiments.database import ExperimentDatabases
from experiments.scenario_types import FrozenModel


class MemoryFacts(FrozenModel):
    """Parse the platform-specific psutil tuple at the collection boundary."""

    total: int


@dataclass(frozen=True, slots=True)
class DatabaseFacts:
    """Server-reported versions, not assumptions inferred from a container tag."""

    postgres_version_num: str
    pgvector_available: str | None


def database_facts(databases: ExperimentDatabases) -> dict[str, str]:
    """Read only version metadata; connection failures never expose credentials."""
    url = databases.url().set(drivername="postgresql").render_as_string(hide_password=False)
    try:
        with psycopg.Connection[DatabaseFacts].connect(
            url, connect_timeout=5, row_factory=class_row(DatabaseFacts)
        ) as connection:
            row = connection.execute(
                """SELECT current_setting('server_version_num') AS postgres_version_num,
                (SELECT default_version FROM pg_available_extensions WHERE name = 'vector')
                AS pgvector_available"""
            ).fetchone()
    except psycopg.Error:
        return {"postgres_version_num": "unavailable", "pgvector_available": "unavailable"}
    if row is None:
        message = "Database version query returned no row"
        raise RuntimeError(message)
    return {
        "postgres_version_num": row.postgres_version_num,
        "pgvector_available": row.pgvector_available or "unavailable",
    }


def git_output(repository: Path, *arguments: str) -> str:
    """Inspect an explicit checkout using argument lists, never a shell command string."""
    executable = shutil.which("git")
    if executable is None:
        message = "Git is required for reproducible runs"
        raise RuntimeError(message)
    result = subprocess.run(  # noqa: S603 -- trusted Git operations and explicit checkout.
        [executable, "-C", str(repository), *arguments],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    return result.stdout.strip()


@dataclass(frozen=True, slots=True)
class GitState:
    """A commit alone cannot identify dirty or untracked executable code."""

    sha: str
    dirty: bool


def git_state(repository: Path) -> GitState:
    """Require all code-affecting checkout changes to remain visible to the runner."""
    return GitState(
        git_output(repository, "rev-parse", "HEAD"),
        bool(git_output(repository, "status", "--porcelain", "--untracked-files=all")),
    )


def runtime_versions() -> dict[str, str]:
    """Record available local runtimes without downloading or changing any toolchain."""
    versions = {"python": platform.python_version()}
    for name in ("uv", "node", "npm", "docker"):
        executable = shutil.which(name)
        if executable is None:
            versions[name] = "unavailable"
            continue
        try:
            result = subprocess.run(  # noqa: S603 -- fixed read-only version query.
                [executable, "--version"],
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            versions[name] = result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            versions[name] = "unavailable"
    versions["browser"] = "not_used_by_experiment"
    return versions


def machine_facts() -> dict[str, JsonValue]:
    """Collect private hardware facts without usernames, environment values or credentials."""
    return {
        "os": platform.system(),
        "os_release": platform.release(),
        "architecture": platform.machine(),
        "cpu": platform.processor(),
        "logical_cpus": psutil.cpu_count(),
        "ram_bytes": MemoryFacts.model_validate(
            psutil.virtual_memory(), from_attributes=True
        ).total,
        "gpu": "not_collected",
        "driver": "not_collected",
        "cache_state": "not reset; use schedule strata, not inferred cold-model claims",
    }
