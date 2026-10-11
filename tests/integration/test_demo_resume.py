from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import anyio
import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncConnection

from rag_access_guard_api.adapters import embeddings
from rag_access_guard_api.demo import documents
from rag_access_guard_api.demo.contracts import DemoConfig, DemoMarker, SeedReport
from rag_access_guard_api.demo.ledger import complete_step
from rag_access_guard_api.demo.mutations import SeedAuthority
from rag_access_guard_api.demo.plan import SeedBinding
from rag_access_guard_api.demo.seed import SeedOptions, seed_demo
from rag_access_guard_api.persistence import (
    AuditEvent,
    DemoSeedStep,
    Document,
    DocumentGrant,
    DocumentVersion,
    PolicyState,
    UserRole,
)
from rag_access_guard_api.server import create_event_loop
from rag_access_guard_api.services.ingestion import PreparedUpload
from tests.helpers.demo_seed import matrix_access, owned_fixture_database, seed_config
from tests.integration.embedding_fixtures import DeterministicEmbedder


@pytest.fixture
def demo_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[URL]:
    monkeypatch.setattr(embeddings, "get_embedding_adapter", DeterministicEmbedder)
    yield from owned_fixture_database(monkeypatch)


def apply(config: DemoConfig, *, initialize: bool = False) -> SeedReport:
    return anyio.run(
        seed_demo,
        config,
        SeedOptions(apply=True, initialize_owned_database=initialize),
        backend_options={"loop_factory": create_event_loop},
    )


def test_complete_matrix_rerun_preserves_independent_direct_grant_and_revocations(
    demo_database: URL, tmp_path: Path
) -> None:
    # Given: 81 external logical objects, including a same-named user and role.
    config = seed_config(tmp_path, demo_database)
    _ = config.access_definition.write_text(matrix_access().model_dump_json(), encoding="utf-8")
    initial = apply(config, initialize=True)
    original = config.credentials_path.read_bytes()
    engine = create_engine(demo_database, hide_parameters=True)
    try:
        with engine.begin() as connection:
            role_grant = connection.execute(
                select(DocumentGrant.id).where(
                    DocumentGrant.role_id.is_not(None),
                    DocumentGrant.role_id
                    == select(DemoSeedStep.object_id)
                    .where(DemoSeedStep.key == "role:reader1")
                    .scalar_subquery(),
                    DocumentGrant.document_id == initial.documents[0].document_id,
                )
            ).scalar_one()
            _ = connection.execute(delete(DocumentGrant).where(DocumentGrant.id == role_grant))
            membership = (
                connection.execute(select(UserRole.user_id, UserRole.role_id)).tuples().first()
            )
            assert membership is not None
            _ = connection.execute(
                delete(UserRole).where(
                    UserRole.user_id == membership[0], UserRole.role_id == membership[1]
                )
            )
            revision = connection.execute(select(PolicyState.revision)).scalar_one()
        # When: the completed dataset is applied again after operator removals.
        repeated = apply(config)
        # Then: no row/version/credential is reset and the independent direct grant remains.
        assert initial.counts.new_objects == 81
        assert initial.counts.new_versions == 24
        assert repeated.counts.new_objects == repeated.counts.new_versions == 0
        assert len(repeated.plan.preserved_operator_changes) == 2
        assert config.credentials_path.read_bytes() == original
        assert initial.report_path != repeated.report_path
        with engine.connect() as connection:
            assert (
                connection.execute(select(func.count()).select_from(DocumentVersion)).scalar_one()
                == 24
            )
            assert connection.execute(select(PolicyState.revision)).scalar_one() == revision
            assert (
                connection.execute(
                    select(func.count())
                    .select_from(DocumentGrant)
                    .where(
                        DocumentGrant.document_id == initial.documents[0].document_id,
                        DocumentGrant.user_id.is_not(None),
                    )
                ).scalar_one()
                == 4
            )
    finally:
        engine.dispose()


def test_two_applies_use_separate_connections_and_one_completed_set(
    demo_database: URL, tmp_path: Path
) -> None:
    # Given: two independent callers released by an actual barrier, not timing sleeps.
    config = seed_config(tmp_path, demo_database)
    barrier = Barrier(2)

    def run() -> SeedReport:
        _ = barrier.wait(timeout=10)
        return apply(config, initialize=True)

    # When: their own engines compete for initialization and the seed coordinator lock.
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(run) for _ in range(2)]
        reports = [future.result(timeout=180) for future in futures]
    # Then: exactly one caller creates the complete logical set and versions.
    assert sorted(report.counts.new_objects for report in reports) == [0, 26]
    assert sorted(report.counts.new_versions for report in reports) == [0, 24]
    assert reports[0].documents == reports[1].documents


def test_document_and_completion_rollback_together(
    demo_database: URL, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a failure after real completion insertion in the first document transaction.
    config = seed_config(tmp_path, demo_database)

    async def fail(connection: AsyncConnection, marker: DemoMarker, binding: SeedBinding) -> None:
        await complete_step(connection, marker, binding)
        message = "controlled transaction interruption"
        raise RuntimeError(message)

    monkeypatch.setattr(documents, "complete_step", fail)
    # When: the normal ingestion transaction reaches that failure.
    with pytest.raises(RuntimeError, match="controlled transaction interruption"):
        _ = apply(config, initialize=True)
    # Then: object/version/completion/audit/revision changes for the document all roll back.
    engine = create_engine(demo_database, hide_parameters=True)
    try:
        with engine.connect() as connection:
            assert connection.execute(select(func.count()).select_from(Document)).scalar_one() == 0
            assert (
                connection.execute(select(func.count()).select_from(DocumentVersion)).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    select(func.count())
                    .select_from(DemoSeedStep)
                    .where(DemoSeedStep.key.like("document:%"))
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(AuditEvent.event_type == "document_changed")
                ).scalar_one()
                == 0
            )
            assert connection.execute(select(PolicyState.revision)).scalar_one() == 2
    finally:
        engine.dispose()


def test_crash_after_document_commit_resumes_without_duplicate_version(
    demo_database: URL, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an interruption immediately after a real document+ledger transaction commits.
    config = seed_config(tmp_path, demo_database)
    original = documents.commit_document_step

    async def crash(
        authority: SeedAuthority,
        step: documents.SeedDocumentStep,
        prepared: PreparedUpload,
    ) -> bool:
        _ = await original(authority, step, prepared)
        message = "controlled postcommit interruption"
        raise RuntimeError(message)

    monkeypatch.setattr(documents, "commit_document_step", crash)
    with pytest.raises(RuntimeError, match="controlled postcommit interruption"):
        _ = apply(config, initialize=True)
    credential_bytes = config.credentials_path.read_bytes()
    monkeypatch.setattr(documents, "commit_document_step", original)
    # When: a new invocation resumes from the durable server binding.
    resumed = apply(config)
    # Then: only the other 23 versions are created and all credentials are recovered.
    assert resumed.counts.new_objects == resumed.counts.new_versions == 23
    assert config.credentials_path.read_bytes() == credential_bytes
    engine = create_engine(demo_database, hide_parameters=True)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(select(func.count()).select_from(DocumentVersion)).scalar_one()
                == 24
            )
    finally:
        engine.dispose()
