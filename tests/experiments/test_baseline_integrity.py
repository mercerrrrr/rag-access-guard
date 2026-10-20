import asyncio
from pathlib import Path

import anyio
import pytest
from experiments.baseline import prepare_baseline
from experiments.baseline_reads import baseline_source
from experiments.comparison import ComparisonConfig
from experiments.harness import ScenarioHarness
from experiments.security_scenarios import load_scenarios
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from rag_access_guard import PrepareDenied
from rag_access_guard_api.adapters.canonical import CanonicalChunk, canonical_query
from rag_access_guard_api.adapters.policy import PostgresPolicyReader
from rag_access_guard_api.persistence import DocumentChunk
from rag_access_guard_api.schemas.search import InvalidProvenanceError
from rag_access_guard_api.schemas.sources import SourceNotFound
from rag_access_guard_api.server import create_event_loop
from rag_access_guard_api.services.grants import revoke_grant
from rag_access_guard_api.services.security import PolicyUnitOfWork


@pytest.mark.parametrize("fault", ["denied_new_candidate", "canonical_failure"])
def test_baseline_preserves_new_candidate_acl_and_canonical_failure(
    fault: str,
    comparison_harness: ScenarioHarness,
    comparison_config: ComparisonConfig,
) -> None:
    async def run() -> None:
        case = next(
            c
            for c in load_scenarios(Path("experiments/scenarios.json"))
            if c.id == "paste-user-only"
        )
        async with (
            comparison_harness.open_pair(case, comparison_config) as pair,
            pair.databases.database(template=pair.template) as (_, url),
        ):
            engine = create_async_engine(url, poolclass=NullPool)
            try:
                async with PolicyUnitOfWork(engine).protected_read(
                    pair.corpus.actors[case.principal_key].token
                ) as uow:
                    row = (
                        (
                            await uow.connection.execute(
                                canonical_query()
                                .where(
                                    DocumentChunk.document_id == pair.corpus.documents["staff"],
                                )
                                .limit(1)
                            )
                        )
                        .mappings()
                        .one()
                    )
                    chunk = CanonicalChunk.model_validate(row).candidate()
                    if fault == "denied_new_candidate":
                        prepared = await prepare_baseline(
                            uow, (chunk,), (), comparison_config, PostgresPolicyReader(uow)
                        )
                        assert isinstance(prepared, PrepareDenied) or not prepared.source_refs
                    else:

                        def corrupt_candidate(self: CanonicalChunk) -> None:
                            del self
                            raise InvalidProvenanceError

                        with pytest.MonkeyPatch.context() as patch:
                            patch.setattr(CanonicalChunk, "candidate", corrupt_candidate)
                            with pytest.raises(SourceNotFound):
                                _ = await baseline_source(uow, chunk.source_ref)
            finally:
                await engine.dispose()

    with asyncio.Runner(loop_factory=create_event_loop) as runner:
        runner.run(run())


@pytest.mark.parametrize("revoked", [False, True], ids=["granted", "revoked"])
def test_baseline_source_reports_observed_revision_without_repeated_grant_gate(
    comparison_harness: ScenarioHarness, comparison_config: ComparisonConfig, *, revoked: bool
) -> None:
    async def run() -> None:
        # Given a canonical source in an isolated experiment with an initial direct grant.
        case = next(
            item
            for item in load_scenarios(Path("experiments/scenarios.json"))
            if item.id == "history-after-direct-revoke"
        )
        async with (
            comparison_harness.open_pair(case, comparison_config) as pair,
            pair.databases.database(template=pair.template) as (_, url),
        ):
            engine = create_async_engine(url, poolclass=NullPool)
            try:
                policy = PolicyUnitOfWork(engine)
                actor = pair.corpus.actors[case.principal_key]
                document_id = pair.corpus.documents["staff"]
                async with policy.protected_read(actor.token) as uow:
                    row = (
                        (
                            await uow.connection.execute(
                                canonical_query()
                                .where(DocumentChunk.document_id == document_id)
                                .limit(1)
                            )
                        )
                        .mappings()
                        .one()
                    )
                    chunk = CanonicalChunk.model_validate(row).candidate()
                    initial_revision = uow.revision
                if revoked:
                    async with policy.mutation(pair.corpus.actors["admin"].token) as mutation:
                        await revoke_grant(
                            mutation,
                            document_id,
                            pair.corpus.grants[("direct", "staff", "student")],
                        )
                async with policy.protected_read(actor.token) as uow:
                    snapshot = await PostgresPolicyReader(uow).snapshot(
                        actor.user_id, (chunk.source_ref,)
                    )
                    # When the experimental baseline reads the valid canonical tuple.
                    source = await baseline_source(uow, chunk.source_ref)
                # Then revision identifies the host transaction, without granting source rights.
                assert source.policy_revision == initial_revision + int(revoked)
                assert source.text == chunk.text
                assert source.document_id == chunk.source_ref.document_id
                assert source.document_version_id == chunk.source_ref.document_version_id
                assert source.chunk_id == chunk.source_ref.chunk_id
                assert snapshot.denied_refs == ((chunk.source_ref,) if revoked else ())
            finally:
                await engine.dispose()

    anyio.run(run, backend_options={"loop_factory": create_event_loop})
