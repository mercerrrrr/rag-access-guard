"""Explicit experimental integrity faults without mutating immutable corpus rows."""

from dataclasses import replace
from uuid import UUID, uuid4

from experiments.scenario_types import TamperProvenance
from rag_access_guard import CandidateChunk, PolicySnapshot, SourceRef
from rag_access_guard_api.adapters.canonical import CanonicalChunk, canonical_query
from rag_access_guard_api.persistence import DocumentChunk
from rag_access_guard_api.services.security import ReadUoW


class UnavailablePolicy:
    """A failing policy adapter used only by the declared outage scenario."""

    async def snapshot(
        self,
        principal_id: UUID,
        source_refs: tuple[SourceRef, ...],
        *,
        thread_id: UUID | None = None,
    ) -> PolicySnapshot:
        """Expose adapter failure to the unchanged production Guard fail-closed path."""
        del principal_id, source_refs, thread_id
        message = "Synthetic policy unavailable"
        raise RuntimeError(message)


async def inject_candidate(
    uow: ReadUoW,
    fault: TamperProvenance,
    documents: dict[str, UUID],
) -> tuple[CandidateChunk, ...]:
    """Attach controlled wrong identity evidence to a real canonical closed passage."""
    row = (
        (
            await uow.connection.execute(
                canonical_query()
                .where(
                    DocumentChunk.document_id == documents[fault.document],
                )
                .order_by(DocumentChunk.ordinal)
                .limit(1)
            )
        )
        .mappings()
        .one()
    )
    closed = CanonicalChunk.model_validate(row).candidate()
    ref = replace(closed.source_ref, chunk_id=uuid4())
    if fault.attached_document is not None:
        attached = (
            (
                await uow.connection.execute(
                    canonical_query()
                    .where(
                        DocumentChunk.document_id == documents[fault.attached_document],
                    )
                    .order_by(DocumentChunk.ordinal)
                    .limit(1)
                )
            )
            .mappings()
            .one()
        )
        ref = CanonicalChunk.model_validate(attached).candidate().source_ref
    return (replace(closed, source_ref=ref),)
