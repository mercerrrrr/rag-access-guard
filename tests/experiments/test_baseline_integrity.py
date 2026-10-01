import asyncio
from pathlib import Path

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
