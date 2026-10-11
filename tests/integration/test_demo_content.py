from collections.abc import Iterator
from hashlib import sha256
from pathlib import Path

import anyio
import pytest
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.engine import URL

from rag_access_guard_api.adapters import embeddings
from rag_access_guard_api.demo.errors import DemoOwnershipError
from rag_access_guard_api.demo.seed import SeedOptions, seed_demo
from rag_access_guard_api.persistence import Document, DocumentVersion, PolicyState
from rag_access_guard_api.server import create_event_loop
from rag_access_guard_api.services.demo_corpus import load_manifest
from tests.helpers.demo_seed import owned_fixture_database, seed_config
from tests.integration.embedding_fixtures import DeterministicEmbedder


@pytest.fixture
def demo_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[URL]:
    monkeypatch.setattr(embeddings, "get_embedding_adapter", DeterministicEmbedder)
    yield from owned_fixture_database(monkeypatch)


def test_content_update_requires_explicit_key_and_creates_one_ready_version(
    demo_database: URL, tmp_path: Path
) -> None:
    # Given: a completed corpus and a newly hash-bound external upload.
    config = seed_config(tmp_path, demo_database)
    initial = anyio.run(
        seed_demo,
        config,
        SeedOptions(apply=True, initialize_owned_database=True),
        backend_options={"loop_factory": create_event_loop},
    )
    original_credentials = config.credentials_path.read_bytes()
    manifest = load_manifest(config.corpus_manifest)
    first = manifest.documents[0]
    path = config.corpus_manifest.parent / first.relative_path
    changed = path.read_bytes() + b"\nAdditional normalized public line.\n"
    _ = path.write_bytes(changed)
    replacement = first.model_copy(update={"upload_sha256": sha256(changed).hexdigest()})
    manifest = manifest.model_copy(update={"documents": (replacement, *manifest.documents[1:])})
    _ = config.corpus_manifest.write_text(manifest.model_dump_json(), encoding="utf-8")
    # When: ordinary apply first refuses the unapproved content conflict.
    with pytest.raises(DemoOwnershipError, match="content_update_required"):
        _ = anyio.run(
            seed_demo,
            config,
            SeedOptions(apply=True),
            backend_options={"loop_factory": create_event_loop},
        )
    # Then: explicit approval creates only one version and preserves logical identity/passwords.
    accepted = anyio.run(
        seed_demo,
        config,
        SeedOptions(apply=True, accepted_content_updates=frozenset({first.key})),
        backend_options={"loop_factory": create_event_loop},
    )
    old = next(binding for binding in initial.documents if binding.key == first.key)
    new = next(binding for binding in accepted.documents if binding.key == first.key)
    assert accepted.counts.new_objects == 0
    assert accepted.counts.new_versions == 1
    assert old.document_id == new.document_id
    assert old.version_id != new.version_id
    assert new.upload_sha256 == replacement.upload_sha256
    assert new.origin_sha256 == old.origin_sha256
    assert config.credentials_path.read_bytes() == original_credentials
    engine = create_engine(demo_database, hide_parameters=True)
    try:
        with engine.connect() as connection:
            assert (
                connection.execute(select(func.count()).select_from(DocumentVersion)).scalar_one()
                == 25
            )
            assert initial.policy_revision_after is not None
            assert (
                connection.execute(select(PolicyState.revision)).scalar_one()
                == initial.policy_revision_after + 1
            )
    finally:
        engine.dispose()


def test_inactive_tracked_document_is_drift_not_reactivated(
    demo_database: URL, tmp_path: Path
) -> None:
    # Given: a completed document deliberately deactivated by its operator.
    config = seed_config(tmp_path, demo_database)
    initial = anyio.run(
        seed_demo,
        config,
        SeedOptions(apply=True, initialize_owned_database=True),
        backend_options={"loop_factory": create_event_loop},
    )
    engine = create_engine(demo_database, hide_parameters=True)
    try:
        with engine.begin() as connection:
            _ = connection.execute(
                update(Document)
                .where(Document.id == initial.documents[0].document_id)
                .values(is_active=False)
            )
            revision = connection.execute(select(PolicyState.revision)).scalar_one()
        # When/Then: apply refuses drift and makes no new version or activation.
        with pytest.raises(DemoOwnershipError, match="tracked_object_drift"):
            _ = anyio.run(
                seed_demo,
                config,
                SeedOptions(apply=True),
                backend_options={"loop_factory": create_event_loop},
            )
        with engine.connect() as connection:
            assert (
                connection.execute(select(func.count()).select_from(DocumentVersion)).scalar_one()
                == 24
            )
            assert connection.execute(select(PolicyState.revision)).scalar_one() == revision
            assert (
                connection.execute(
                    select(Document.is_active).where(
                        Document.id == initial.documents[0].document_id
                    )
                ).scalar_one()
                is False
            )
    finally:
        engine.dispose()
