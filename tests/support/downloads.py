from dataclasses import dataclass
from uuid import UUID

import pytest
from sqlalchemy import select
from tests.support.chat_generation import ChatCase

from rag_access_guard import SourceRef
from rag_access_guard_api.persistence import Document, DocumentChunk, DocumentVersion
from rag_access_guard_api.services.sources import build_source_url


@dataclass(frozen=True, slots=True)
class DownloadCase:
    case: ChatCase
    ref: SourceRef
    original_bytes: bytes

    @property
    def content_url(self) -> str:
        return build_source_url(self.ref)

    @property
    def original_url(self) -> str:
        return self.content_url.replace("/content?", "/original?")


def source_ref(case: ChatCase, document_id: UUID) -> SourceRef:
    with case.database.connect() as connection:
        row = (
            connection.execute(
                select(
                    DocumentChunk.document_id, DocumentChunk.document_version_id, DocumentChunk.id
                )
                .join(Document, Document.active_version_id == DocumentChunk.document_version_id)
                .where(DocumentChunk.document_id == document_id)
                .order_by(DocumentChunk.ordinal)
                .limit(1)
            )
            .tuples()
            .one()
        )
    return SourceRef(document_id=row[0], document_version_id=row[1], chunk_id=row[2])


@pytest.fixture
def download_case(chat_case: ChatCase) -> DownloadCase:
    ref = source_ref(chat_case, chat_case.document.id)
    with chat_case.database.connect() as connection:
        raw = connection.execute(
            select(DocumentVersion.original_bytes).where(
                DocumentVersion.id == ref.document_version_id
            )
        ).scalar_one()
    return DownloadCase(chat_case, ref, raw)
