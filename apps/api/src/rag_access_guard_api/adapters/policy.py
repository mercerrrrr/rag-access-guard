"""Policy snapshots bound to one host-owned read transaction."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, select

from rag_access_guard import PolicySnapshot, SourceRef
from rag_access_guard_api.adapters.canonical import CanonicalChunk, canonical_query
from rag_access_guard_api.persistence import ChatThread, Document, DocumentChunk, DocumentVersion
from rag_access_guard_api.schemas.search import InvalidProvenanceError
from rag_access_guard_api.services.access import readable_document
from rag_access_guard_api.services.security import ReadUoW


class AuthorizedChunk(CanonicalChunk):
    """Canonical row plus authorization from the same policy snapshot."""

    allowed: bool


@dataclass(frozen=True, slots=True)
class PostgresPolicyReader:
    """Resolve complete source identities without creating a new transaction."""

    uow: ReadUoW

    async def snapshot(
        self,
        principal_id: UUID,
        source_refs: tuple[SourceRef, ...],
        *,
        thread_id: UUID | None = None,
    ) -> PolicySnapshot:
        """Partition exact tuples and bind every allowed reference to its stored hash."""
        requested = tuple(dict.fromkeys(source_refs))
        live = (
            not self.uow.connection.closed
            and self.uow.connection.in_transaction()
            and principal_id == self.uow.principal.principal_id
        )
        owned = None
        if thread_id is not None:
            owned = (
                live
                and (
                    await self.uow.connection.execute(
                        select(ChatThread.id).where(
                            ChatThread.id == thread_id,
                            ChatThread.owner_user_id == principal_id,
                        )
                    )
                ).scalar_one_or_none()
                is not None
            )
        allowed: list[SourceRef] = []
        denied: list[SourceRef] = []
        hashes: list[tuple[SourceRef, str]] = []
        valid = live
        if live:
            for ref in requested:
                query = (
                    canonical_query()
                    .join(Document, Document.id == DocumentChunk.document_id)
                    .add_columns(
                        and_(
                            readable_document(principal_id),
                            Document.active_version_id == DocumentChunk.document_version_id,
                            DocumentVersion.status == "ready",
                        ).label("allowed")
                    )
                    .where(
                        DocumentChunk.id == ref.chunk_id,
                        DocumentChunk.document_id == ref.document_id,
                        DocumentChunk.document_version_id == ref.document_version_id,
                    )
                )
                row = (await self.uow.connection.execute(query)).mappings().one_or_none()
                if row is None:
                    valid = False
                    denied.append(ref)
                    continue
                canonical = AuthorizedChunk.model_validate(row)
                try:
                    candidate = canonical.candidate()
                except InvalidProvenanceError:
                    valid = False
                    denied.append(ref)
                    continue
                if canonical.allowed and owned is not False:
                    allowed.append(ref)
                    hashes.append((ref, candidate.content_sha256))
                else:
                    denied.append(ref)
        else:
            denied.extend(requested)
        return PolicySnapshot(
            principal_id=principal_id,
            revision=self.uow.revision,
            allowed_refs=tuple(allowed),
            denied_refs=tuple(denied),
            provenance_valid=valid,
            principal_active=live,
            thread_owned=owned,
            canonical_chunk_hashes=tuple(hashes),
        )
