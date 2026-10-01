"""Versioned identities for private, reproducible experiment records."""

import json
from datetime import datetime
from hashlib import sha256
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, JsonValue

from experiments.observations import Arm
from experiments.scenario_types import FrozenModel

type Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class RunIdentity(FrozenModel):
    """Every controlled input required to reproduce a pair schedule."""

    git_sha: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    config: dict[str, JsonValue]
    corpus_hash: Digest
    scenario_hash: Digest
    lock_hashes: dict[str, Digest]
    model_tag: str
    model_digest: Digest
    runtime_config_hash: Digest
    trial_config_hashes: dict[str, Digest] = Field(default_factory=dict)
    runtime_versions: dict[str, str]


class TrialSpec(FrozenModel):
    """A scheduled pair, including warmups that never enter the main sample."""

    trial_id: str
    case_id: str
    repetition: Annotated[int, Field(ge=0)]
    seed: int
    warmup: bool
    order: tuple[Arm, Arm]
    cache_state: str


class RunCounts(FrozenModel):
    """Missing and failed pairs remain visible alongside completed observations."""

    scheduled: Annotated[int, Field(ge=0)] = 0
    completed: Annotated[int, Field(ge=0)] = 0
    failed: Annotated[int, Field(ge=0)] = 0
    invalid: Annotated[int, Field(ge=0)] = 0
    attempts: Annotated[int, Field(ge=0)] = 0


class RunManifest(FrozenModel):
    """Private run lifecycle; only clean, fully recorded pairs can be comparable."""

    schema_version: Literal[1] = 1
    run_id: UUID
    status: Literal["not_run", "running", "completed", "failed", "invalid"]
    reproduces_run_id: UUID | None = None
    started_at: datetime
    finished_at: datetime | None = None
    identity: RunIdentity
    dirty: bool
    environment: dict[str, JsonValue]
    machine_differences: tuple[str, ...] = ()
    schedule: tuple[TrialSpec, ...]
    counts: RunCounts = RunCounts()
    records_sha256: Digest | None = None
    reason: str | None = None
    live_status: Literal["not_run", "running", "completed", "failed"] = "not_run"
    live_reason: str | None = "Synthetic structural run; live model not requested"


def canonical_config_hash(config: JsonValue) -> str:
    """Hash all nested settings independently of JSON object insertion order."""
    return sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def validate_reproduction(original: RunManifest, current: RunManifest) -> None:
    """Reject changed controlled inputs; machine differences are recorded separately."""
    if original.status != "completed":
        message = "Reproduction requires a completed original run"
        raise ValueError(message)
    if original.dirty or current.dirty:
        message = "Reproduction requires clean code"
        raise ValueError(message)
    if original.identity != current.identity or original.schedule != current.schedule:
        message = "Reproduction controlled inputs mismatch"
        raise ValueError(message)
