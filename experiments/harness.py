"""Disposable PostgreSQL scenario execution around the shared experiment host."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import httpx2 as httpx
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from experiments.comparison_config import ComparisonConfig
from experiments.database import ExperimentDatabases, migrate
from experiments.fixture_manifest import FixtureManifest, load_fixture_manifest
from experiments.host import ExperimentHost
from experiments.mutations import Mutations
from experiments.observations import Arm, ArmRecord, ArmResult
from experiments.ordering import Ordering
from experiments.scenario_types import (
    Ask,
    DirectRevoke,
    PolicyFailure,
    ReadDocuments,
    ReadSource,
    ReadThread,
    RemoveMembership,
    ReplaceVersion,
    RoleRevoke,
    Scenario,
    SessionChange,
    TamperProvenance,
)
from experiments.seed import SeededCorpus, seed_corpus
from experiments.transport import capture_http, create_transport
from rag_access_guard import SourceRef
from rag_access_guard_api.services.security import PolicyUnitOfWork


async def _ask(
    host: ExperimentHost,
    client: httpx.AsyncClient,
    case: Scenario,
    action: Ask,
    mutations: Mutations,
) -> httpx.Response:
    concurrent = next(
        (
            a
            for a in case.actions
            if isinstance(a, DirectRevoke)
            and a.barrier is not None
            and a.barrier.ask_id == action.id
        ),
        None,
    )
    ordering = Ordering(concurrent, mutations) if concurrent is not None else None
    host.ordering = ordering
    timeout = 30 if host.config.model_manifest is None else 150
    async with asyncio.timeout(timeout), asyncio.TaskGroup() as group:
        if ordering is not None:
            _ = group.create_task(ordering.change())
        return await client.post(
            "/api/chat/messages",
            json={"action_id": action.id, "user_input": action.user_input or case.user_input},
        )


@dataclass(frozen=True, slots=True)
class PairHarness:
    """A closed canonical template cloned independently for each arm."""

    databases: ExperimentDatabases
    template: str
    corpus: SeededCorpus
    corpus_hash: str
    root: Path
    manifest: FixtureManifest
    arm_records: list[ArmRecord] = field(default_factory=list)

    def _result(self, host: ExperimentHost, config_hash: str) -> ArmResult:
        return ArmResult(
            arm=host.arm,
            config_hash=config_hash,
            corpus_hash=self.corpus_hash,
            renderer_identity=host.config.renderer_identity,
            tokenizer_identity=host.config.tokenizer_identity,
            model_identity=host.config.model_identity,
            observations=tuple(host.observations),
            documents=tuple(sorted(self.corpus.documents.items())),
            commit_order=tuple(host.ordering.commits) if host.ordering is not None else (),
            attempts=tuple(host.attempts),
        )

    def _record(
        self, host: ExperimentHost | None, result: ArmResult | None, config_hash: str
    ) -> None:
        if host is not None:
            self.arm_records.append(
                ArmRecord(
                    status="completed" if result is not None else "failed",
                    result=result or self._result(host, config_hash),
                )
            )

    async def run_arm(self, case: Scenario, config: ComparisonConfig, arm: Arm) -> ArmResult:
        """Run ordered actions against a private clone and return observed evidence."""
        config_hash = config.runtime_digest()
        async with self.databases.database(template=self.template) as (_, url):
            engine = create_async_engine(url, poolclass=NullPool, hide_parameters=True)
            host: ExperimentHost | None = None
            result: ArmResult | None = None
            try:
                policy = PolicyUnitOfWork(engine)
                host = ExperimentHost(
                    policy,
                    self.corpus,
                    self.corpus.actors[case.principal_key],
                    config,
                    arm,
                    synthetic_markers=frozenset(case.synthetic_markers),
                )
                mutations = Mutations(engine, self.corpus, host.actor, self.root, self.manifest)
                async with httpx.AsyncClient(
                    transport=create_transport(host),
                    base_url="http://experiment.test",
                    http2=True,
                    timeout=30,
                    follow_redirects=True,
                ) as client:
                    for action in case.actions:
                        response = None
                        match action:
                            case Ask():
                                response = await _ask(host, client, case, action, mutations)
                            case DirectRevoke(barrier=barrier) if barrier is not None:
                                pass
                            case (
                                DirectRevoke(barrier=None)
                                | RoleRevoke()
                                | RemoveMembership()
                                | ReplaceVersion()
                                | SessionChange()
                            ):
                                await mutations.apply(action)
                            case TamperProvenance():
                                host.fault = action
                            case PolicyFailure():
                                host.policy_failure = True
                            case ReadDocuments():
                                response = await client.get(
                                    "/api/documents", params={"action_id": action.id}
                                )
                            case ReadThread():
                                response = await client.get(
                                    "/api/chat/thread",
                                    params={
                                        "action_id": action.id,
                                        "reader": action.reader or case.principal_key,
                                    },
                                )
                            case ReadSource():
                                ref = (
                                    SourceRef(
                                        document_id=uuid4(),
                                        document_version_id=uuid4(),
                                        chunk_id=uuid4(),
                                    )
                                    if action.unknown
                                    else next(
                                        ref
                                        for ref in host.witnesses[action.ask_id or ""]
                                        if ref.document_id == self.corpus.documents[action.document]
                                    )
                                )
                                response = await client.get(
                                    "/api/documents/source",
                                    params={
                                        "action_id": action.id,
                                        "document_id": str(ref.document_id),
                                        "version_id": str(ref.document_version_id),
                                        "chunk_id": str(ref.chunk_id),
                                    },
                                )
                            case _:
                                message = f"Unsupported experiment action: {action.kind}"
                                raise ValueError(message)
                        capture_http(host, action.id, response)
                _ = config.runtime_digest(expected=config_hash)
                result = self._result(host, config_hash)
                return result
            finally:
                self._record(host, result, config_hash)
                await engine.dispose()


@dataclass(frozen=True, slots=True)
class ScenarioHarness:
    """Create canonical snapshots with real ingestion, migrations and server sessions."""

    databases: ExperimentDatabases
    manifest_path: Path = Path("experiments/fixture_manifest.json")

    @asynccontextmanager
    async def open_pair(
        self, case: Scenario, config: ComparisonConfig
    ) -> AsyncGenerator[PairHarness]:
        """Validate fixture bytes before opening any database and clone one seeded corpus."""
        del config
        manifest = await asyncio.to_thread(load_fixture_manifest, self.manifest_path)
        digest = sha256(manifest.model_dump_json().encode()).hexdigest()
        async with self.databases.database() as (name, url):
            await asyncio.to_thread(migrate, url)
            engine = create_async_engine(url, poolclass=NullPool, hide_parameters=True)
            try:
                corpus = await seed_corpus(engine, case, manifest, self.manifest_path.parent)
            finally:
                await engine.dispose()
            yield PairHarness(
                self.databases, name, corpus, digest, self.manifest_path.parent, manifest
            )
