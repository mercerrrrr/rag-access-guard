"""Owned-target and actor-visible canonical gold checks without repair or seeding."""

from hashlib import sha256

from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import AsyncConnection

from rag_access_guard_api.adapters.canonical import CanonicalChunk, canonical_query
from rag_access_guard_api.demo.bootstrap import inspect_target
from rag_access_guard_api.demo.contracts import DemoConfig, DemoMarker
from rag_access_guard_api.demo.evaluation import DemoDatasetError
from rag_access_guard_api.demo.evaluation_contracts import DemoEvaluationCase
from rag_access_guard_api.demo.ownership import validate_owned_target, validate_target_locator
from rag_access_guard_api.persistence import DemoSeedState, Document, DocumentChunk, DocumentVersion
from rag_access_guard_api.services.access import readable_document
from rag_access_guard_api.services.security import ReadUoW


def verify_owned_evaluation_target(config: DemoConfig) -> DemoMarker:
    """Verify actual server role, marker and exact local target before authentication."""
    _ = validate_target_locator(config)
    engine = create_engine(
        config.database_url.get_secret_value(),
        hide_parameters=True,
        connect_args={"connect_timeout": 2},
        pool_size=1,
        max_overflow=0,
    )
    try:
        with engine.connect() as connection:
            marker = inspect_target(connection, config)
            validate_owned_target(config, marker)
            if marker is None:
                raise DemoDatasetError(code="marker_absent")
            return marker
    finally:
        engine.dispose()


async def verify_marker(connection: AsyncConnection, marker: DemoMarker) -> None:
    """Recheck the marker inside each policy-gated scientific read snapshot."""
    row = (
        (
            await connection.execute(
                select(
                    DemoSeedState.instance_id,
                    DemoSeedState.dataset_id,
                    DemoSeedState.database_name,
                    DemoSeedState.database_role,
                )
            )
        )
        .tuples()
        .one_or_none()
    )
    if row is None or tuple(row) != (
        marker.instance_id,
        marker.dataset_id,
        marker.database_name,
        marker.database_role,
    ):
        raise DemoDatasetError(code="marker_mismatch")


async def validate_case_evidence(uow: ReadUoW, case: DemoEvaluationCase) -> None:
    """Use current grants even for admins; stale/inaccessible gold is not a candidate."""
    if uow.user.login != case.principal_login:
        raise DemoDatasetError(code="case_actor_mismatch")
    for alternative in case.evidence_sets:
        for evidence in alternative.evidence:
            row = (
                (
                    await uow.connection.execute(
                        canonical_query()
                        .join(Document, Document.id == DocumentChunk.document_id)
                        .where(
                            DocumentChunk.id == evidence.chunk_id,
                            DocumentChunk.document_id == evidence.document_id,
                            DocumentChunk.document_version_id == evidence.version_id,
                            Document.active_version_id == evidence.version_id,
                            DocumentVersion.status == "ready",
                            readable_document(uow.principal.principal_id),
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise DemoDatasetError(code="inaccessible_or_stale_evidence")
            chunk = CanonicalChunk.model_validate(row)
            _ = chunk.candidate()
            span = chunk.text[evidence.span_start : evidence.span_end]
            if (
                span != evidence.supporting_text
                or sha256(span.encode()).hexdigest() != evidence.supporting_sha256
            ):
                raise DemoDatasetError(code="canonical_evidence_mismatch")
    versions = (
        (
            await uow.connection.execute(
                select(Document.active_version_id)
                .join(DocumentVersion, DocumentVersion.id == Document.active_version_id)
                .where(
                    readable_document(uow.principal.principal_id), DocumentVersion.status == "ready"
                )
            )
        )
        .scalars()
        .all()
    )
    if set(versions) != set(case.allowed_version_ids):
        raise DemoDatasetError(code="allowed_version_drift")
