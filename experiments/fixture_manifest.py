"""Synthetic fixture identities, independent of database-generated SourceRefs."""

from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from experiments.scenario_types import FrozenModel, Key


class Fixture(FrozenModel):
    """Bytes and independently named facts for one synthetic document."""

    key: Key
    path: Annotated[str, Field(pattern=r"^fixtures/[a-z][a-z0-9_-]*\.txt$")]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    fact_keys: Annotated[tuple[Key, ...], Field(min_length=1)]


class FixtureManifest(FrozenModel):
    """Versioned corpus declaration containing no runtime source identities."""

    schema_version: Literal[1]
    fixtures: Annotated[tuple[Fixture, ...], Field(min_length=1)]


def load_fixture_manifest(path: Path) -> FixtureManifest:
    """Read the corpus declaration before any experiment connects to a host."""
    manifest = FixtureManifest.model_validate_json(path.read_bytes())
    keys = [fixture.key for fixture in manifest.fixtures]
    if len(keys) != len(set(keys)):
        message = "duplicate fixture key"
        raise ValueError(message)
    for fixture in manifest.fixtures:
        content = (path.parent / fixture.path).read_bytes()
        _ = content.decode("utf-8")
        if sha256(content).hexdigest() != fixture.sha256:
            message = f"fixture hash mismatch: {fixture.key}"
            raise ValueError(message)
        if len(set(fixture.fact_keys)) != len(fixture.fact_keys) or any(
            not fact.startswith(f"{fixture.key}.") for fact in fixture.fact_keys
        ):
            message = f"invalid fixture fact keys: {fixture.key}"
            raise ValueError(message)
    return manifest
