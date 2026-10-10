import logging
from dataclasses import replace

import pytest
from sqlalchemy import text

from rag_access_guard_api.services.sources import build_source_url
from tests.integration.test_chat_history_boundaries import add_document
from tests.integration.test_docx_access import upload_docx
from tests.integration.test_source_boundaries import assert_hidden
from tests.support.downloads import DownloadCase, source_ref


def with_format(download_case: DownloadCase, kind: str) -> DownloadCase:
    if kind == "text":
        return download_case
    _, raw = upload_docx(download_case.case)
    return DownloadCase(
        download_case.case, source_ref(download_case.case, download_case.case.document.id), raw
    )


@pytest.mark.parametrize("kind", ["text", "docx"])
@pytest.mark.parametrize("field", ["document_id", "document_version_id", "chunk_id"])
def test_original_requires_one_complete_canonical_chain(
    download_case: DownloadCase, field: str, kind: str
) -> None:
    download_case = with_format(download_case, kind)
    case = download_case.case
    other = source_ref(case, add_document(case).id)
    mixed = {
        "document_id": replace(download_case.ref, document_id=other.document_id),
        "document_version_id": replace(
            download_case.ref, document_version_id=other.document_version_id
        ),
        "chunk_id": replace(download_case.ref, chunk_id=other.chunk_id),
    }[field]
    url = build_source_url(mixed).replace("/content?", "/original?")
    assert_hidden(case.client.get(url))


@pytest.mark.parametrize("kind", ["text", "docx"])
@pytest.mark.parametrize("fault", ["bytes", "manifest", "not_ready"])
def test_original_fails_closed_on_corrupt_storage(
    download_case: DownloadCase, fault: str, caplog: pytest.LogCaptureFixture, kind: str
) -> None:
    download_case = with_format(download_case, kind)
    caplog.set_level(logging.DEBUG, logger="rag_access_guard_api")
    case = download_case.case
    with case.database.begin() as connection:
        _ = connection.execute(text("SET LOCAL session_replication_role = replica"))
        if fault == "bytes":
            _ = connection.execute(
                text(
                    """ALTER TABLE document_versions
                    DROP CONSTRAINT ck_document_versions_content_hash"""
                )
            )
            statement = """UPDATE document_versions
                    SET original_bytes=set_byte(original_bytes,:offset,88) WHERE id=:id"""
        elif fault == "manifest":
            _ = connection.execute(
                text("ALTER TABLE document_versions DROP CONSTRAINT ck_document_versions_manifest")
            )
            statement = "UPDATE document_versions SET ingestion_manifest='{}'::jsonb WHERE id=:id"
        else:
            statement = "UPDATE document_versions SET status='indexing' WHERE id=:id"
        _ = connection.execute(
            text(statement),
            {"id": download_case.ref.document_version_id, "offset": 4 if kind == "docx" else 0},
        )
    assert_hidden(case.client.get(download_case.original_url))
    assert "PROTECTED_SYNTHETIC" not in caplog.text
    assert case.document.title not in caplog.text
    if fault == "bytes":
        assert "original_integrity_failed" in caplog.text
