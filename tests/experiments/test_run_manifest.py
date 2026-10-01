from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from experiments.run_manifest import (
    RunIdentity,
    RunManifest,
    canonical_config_hash,
    validate_reproduction,
)

if TYPE_CHECKING:
    from pydantic import JsonValue


def test_config_hash_changes_when_tokenizer_changes() -> None:
    first: dict[str, JsonValue] = {
        "tokenizer": {"id": "synthetic", "revision": "a"},
        "max_context_tokens": 5000,
        "max_prior_turns": 4,
    }
    second: dict[str, JsonValue] = {**first, "tokenizer": {"id": "synthetic", "revision": "b"}}
    assert canonical_config_hash(first) != canonical_config_hash(second)
    assert canonical_config_hash(first) == canonical_config_hash(
        dict(reversed(list(first.items())))
    )


@pytest.fixture
def manifest() -> RunManifest:
    return RunManifest(
        run_id=uuid4(),
        status="completed",
        started_at=datetime.now(UTC),
        identity=RunIdentity(
            git_sha="a" * 40,
            config={"tokenizer": "synthetic-v1"},
            corpus_hash="b" * 64,
            scenario_hash="c" * 64,
            lock_hashes={"uv.lock": "d" * 64},
            model_tag="synthetic",
            model_digest="e" * 64,
            runtime_config_hash="f" * 64,
            runtime_versions={"python": "3.13.15"},
        ),
        dirty=False,
        environment={"cpu": "one"},
        schedule=(),
    )


@pytest.mark.parametrize(
    "field",
    [
        "model_digest",
        "corpus_hash",
        "scenario_hash",
        "runtime_config_hash",
        "git_sha",
        "config",
        "lock_hashes",
        "runtime_versions",
    ],
)
def test_reproduction_rejects_controlled_mismatch(manifest: RunManifest, field: str) -> None:
    changes: dict[str, object] = {
        "model_digest": "1" * 64,
        "corpus_hash": "2" * 64,
        "scenario_hash": "3" * 64,
        "runtime_config_hash": "4" * 64,
        "git_sha": "5" * 40,
        "config": {"tokenizer": "different"},
        "lock_hashes": {"uv.lock": "6" * 64},
        "runtime_versions": {"python": "different"},
    }
    changed = manifest.model_copy(
        update={"identity": manifest.identity.model_copy(update={field: changes[field]})}
    )
    with pytest.raises(ValueError, match="controlled"):
        validate_reproduction(manifest, changed)


@pytest.mark.parametrize("status", ["failed", "invalid", "running", "not_run"])
def test_only_completed_runs_can_be_reproduced(manifest: RunManifest, status: str) -> None:
    with pytest.raises(ValueError, match="completed"):
        validate_reproduction(manifest.model_copy(update={"status": status}), manifest)


def test_dirty_tree_cannot_produce_comparable_run(manifest: RunManifest) -> None:
    with pytest.raises(ValueError, match="clean"):
        validate_reproduction(manifest, manifest.model_copy(update={"dirty": True}))


def test_machine_difference_does_not_change_controlled_identity(manifest: RunManifest) -> None:
    validate_reproduction(manifest, manifest.model_copy(update={"environment": {"cpu": "two"}}))
