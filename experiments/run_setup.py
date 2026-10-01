"""Bind a run to actual input bytes and the checkout that will execute them."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic import JsonValue

    from experiments.scenario_types import Scenario

from sqlalchemy.engine import make_url

from experiments.database import ExperimentDatabases
from experiments.fixture_manifest import load_fixture_manifest
from experiments.run_config import RunConfig, RunOptions
from experiments.run_environment import (
    GitState,
    database_facts,
    git_state,
    machine_facts,
    runtime_versions,
)
from experiments.run_manifest import RunIdentity, RunManifest, canonical_config_hash
from experiments.run_schedule import schedule_trials
from experiments.security_scenarios import load_scenarios
from experiments.seed import SYNTHETIC_RECIPE
from rag_access_guard_api.adapters.embeddings import MODEL_ID
from rag_access_guard_api.adapters.tokenizer import MODEL_REVISION, TOKENIZER_IDENTITY
from rag_access_guard_api.config import Settings
from rag_access_guard_api.services.chunking import CHUNKER_REVISION
from rag_access_guard_api.services.model_manifest import OLLAMA_VERSION, ModelManifest


@dataclass(frozen=True, slots=True)
class RunInputs:
    """Immutable input snapshot used before execution and at final admission."""

    repository: Path
    options: RunOptions
    config: RunConfig
    cases: tuple[Scenario, ...]
    corpus_hash: str
    hashes: tuple[tuple[Path, str], ...]
    code: GitState

    @classmethod
    def load(cls, options: RunOptions, repository: Path) -> RunInputs:
        """Parse the requested files and bind all corpus bytes before database work."""
        code = git_state(repository)
        manifest_path = options.scenarios.with_name("fixture_manifest.json")
        before = {
            path: sha256(path.read_bytes()).hexdigest()
            for path in (options.config, options.scenarios, manifest_path)
        }
        config = RunConfig.model_validate_json(options.config.read_bytes())
        config.comparison.validate_runtime()
        if options.llm == "fake" and config.comparison.model_manifest is not None:
            message = "Fake run cannot use a live model identity"
            raise ValueError(message)
        if options.llm == "ollama" and config.comparison.model_manifest is None:
            model = ModelManifest()
            comparison = config.comparison.model_copy(
                update={
                    "model_manifest": model,
                    "model_identity": f"{model.model_name}@{model.model_digest}",
                    "tokenizer_identity": sha256(model.model_dump_json().encode()).hexdigest(),
                }
            )
            config = config.model_copy(update={"comparison": comparison})
        config.comparison.validate_runtime()
        cases = load_scenarios(options.scenarios)
        if not cases:
            message = "A run requires at least one scenario"
            raise ValueError(message)
        corpus = load_fixture_manifest(manifest_path)
        paths = (
            options.config,
            options.scenarios,
            manifest_path,
            *(manifest_path.parent / fixture.path for fixture in corpus.fixtures),
            repository / "uv.lock",
            repository / "apps/web/package-lock.json",
            repository / "compose.yaml",
        )
        hashes = tuple((path, sha256(path.read_bytes()).hexdigest()) for path in paths)
        if any(dict(hashes)[path] != digest for path, digest in before.items()):
            message = "Inputs changed while loading"
            raise ValueError(message)
        return cls(
            repository,
            options,
            config,
            cases,
            sha256(corpus.model_dump_json().encode()).hexdigest(),
            hashes,
            code,
        )

    def unchanged(self) -> bool:
        """Reject code or input drift during a run rather than claim reproducibility."""
        return git_state(self.repository) == self.code and all(
            sha256(path.read_bytes()).hexdigest() == digest for path, digest in self.hashes
        )

    def manifest(self) -> RunManifest:
        """Describe the actual synthetic adapters separately from production recipes."""
        model = self.config.comparison.model_manifest
        schedule = schedule_trials(
            tuple(case.id for case in self.cases),
            seed=self.options.seed,
            repetitions=self.options.repetitions,
            warmups=self.config.warmup_repetitions,
        )
        controlled: dict[str, JsonValue] = {
            "settings": self.config.model_dump(mode="json"),
            "seed": self.options.seed,
            "repetitions": self.options.repetitions,
            "llm": self.options.llm,
            "embedding": {
                "actual": SYNTHETIC_RECIPE,
                "recipe_model": MODEL_ID,
                "recipe_revision": MODEL_REVISION,
            },
            "ingestion_tokenizer": TOKENIZER_IDENTITY,
            "chunker": CHUNKER_REVISION,
            "guarded_retry_count": 1,
            "baseline_retry_count": 0,
            "model_randomness": (
                "none: synthetic deterministic markers"
                if model is None
                else "Ollama model defaults; generation seed not set; not bitwise deterministic"
            ),
            "request_timeout_seconds": 30 if model is None else 150,
            "cache_reset": False,
        }
        hashes = dict(self.hashes)
        database = make_url(str(Settings().database_url)).set(database="postgres")
        observed_database = database_facts(
            ExperimentDatabases(database.render_as_string(hide_password=False))
        )
        controlled["config_hash"] = canonical_config_hash(controlled.copy())
        return RunManifest(
            run_id=uuid4(),
            status="running",
            started_at=datetime.now(UTC),
            identity=RunIdentity(
                git_sha=self.code.sha,
                config=controlled,
                corpus_hash=self.corpus_hash,
                scenario_hash=hashes[self.options.scenarios],
                lock_hashes={
                    name: hashes[self.repository / name]
                    for name in ("uv.lock", "apps/web/package-lock.json", "compose.yaml")
                },
                model_tag=self.config.comparison.model_identity
                if model is None
                else model.model_name,
                model_digest=sha256(self.config.comparison.model_identity.encode()).hexdigest()
                if model is None
                else model.model_digest,
                runtime_config_hash=self.config.comparison.runtime_digest(),
                trial_config_hashes={
                    trial.trial_id: self.config.comparison.model_copy(
                        update={"seed": trial.seed}
                    ).runtime_digest()
                    for trial in schedule
                },
                runtime_versions={
                    **runtime_versions(),
                    **observed_database,
                    "ollama_required": "not_used" if model is None else OLLAMA_VERSION,
                },
            ),
            dirty=self.code.dirty,
            live_status="not_run" if model is None else "running",
            live_reason="Synthetic structural run; live model not requested"
            if model is None
            else None,
            environment=machine_facts(),
            schedule=schedule,
        )
