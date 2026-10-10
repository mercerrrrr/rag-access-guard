from concurrent.futures import ThreadPoolExecutor
from time import monotonic
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from pydantic import TypeAdapter
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import DBAPIError

from rag_access_guard_api.persistence import DocumentOriginRecord
from rag_access_guard_api.schemas.documents import DocumentSummary
from rag_access_guard_api.services.origin import canonical_origin_hash
from tests.integration.test_document_origin import synthetic_origin, upload_origin
from tests.support.chat_generation import ChatCase


def test_origin_downgrade_refuses_existing_metadata(
    chat_case: ChatCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    ref = upload_origin(chat_case, monkeypatch, synthetic_origin())
    query = select(
        DocumentOriginRecord.version_id,
        DocumentOriginRecord.origin,
        DocumentOriginRecord.origin_sha256,
    )
    with chat_case.database.connect() as connection:
        before = connection.execute(query).tuples().one()
    # When / Then
    with pytest.raises(DBAPIError, match="origin downgrade requires an empty origin table"):
        command.downgrade(Config("apps/api/alembic.ini"), "0012_docx_ingestion")
    with chat_case.database.connect() as connection:
        assert connection.execute(query).tuples().one() == before
        assert before[0] == ref.document_version_id
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "0013_document_origin"
        )


def test_origin_downgrade_preserves_concurrently_committed_metadata(
    registered_document: DocumentSummary, auth_database: Engine
) -> None:
    # Given a normal valid new-version/origin insert pending in its own transaction.
    version_id = uuid4()
    origin = synthetic_origin()
    error: DBAPIError | None = None
    with auth_database.connect() as writer, ThreadPoolExecutor(max_workers=1) as executor:
        _ = writer.execute(
            text("""INSERT INTO document_versions
            (id,document_id,original_bytes,content_sha256,extracted_text,text_sha256,
             media_type,byte_size,parser_revision,status,ingestion_manifest,created_by)
            SELECT :new_id,document_id,original_bytes,content_sha256,extracted_text,text_sha256,
             media_type,byte_size,parser_revision,'stored',ingestion_manifest,created_by
            FROM document_versions WHERE id=:old_id"""),
            {"new_id": version_id, "old_id": registered_document.active_version_id},
        )
        _ = writer.execute(
            text("""INSERT INTO document_origins(version_id,origin,origin_sha256)
            VALUES (:id,CAST(:origin AS jsonb),:hash)"""),
            {
                "id": version_id,
                "origin": origin.model_dump_json(),
                "hash": canonical_origin_hash(origin),
            },
        )
        backend = TypeAdapter(int).validate_python(
            writer.execute(text("SELECT pg_backend_pid()")).scalar_one()
        )
        # When the actual downgrade reaches an origin-table lock blocked by that insert.
        downgrade = executor.submit(
            command.downgrade, Config("apps/api/alembic.ini"), "0012_docx_ingestion"
        )
        try:
            deadline = monotonic() + 10
            with auth_database.connect() as observer:
                while not observer.execute(
                    text("""SELECT EXISTS(
                    SELECT 1 FROM pg_stat_activity a JOIN pg_locks l ON l.pid=a.pid
                    WHERE a.datname=current_database() AND :blocker=ANY(pg_blocking_pids(a.pid))
                      AND a.wait_event_type='Lock' AND l.relation='document_origins'::regclass
                      AND l.mode='AccessExclusiveLock' AND NOT l.granted)"""),
                    {"blocker": backend},
                ).scalar_one():
                    assert monotonic() < deadline, (
                        "Downgrade did not wait on the pending origin insert"
                    )
                    observer.rollback()
        finally:
            writer.commit()
        try:
            downgrade.result(10)
        except DBAPIError as caught:
            error = caught
    # Then refusal preserves both committed metadata and the schema head.
    with auth_database.connect() as connection:
        head = TypeAdapter(str).validate_python(
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        )
        assert head == "0013_document_origin", (
            "Downgrade removed concurrently committed origin metadata"
        )
        row = (
            connection.execute(
                select(DocumentOriginRecord.origin, DocumentOriginRecord.origin_sha256).where(
                    DocumentOriginRecord.version_id == version_id
                )
            )
            .tuples()
            .one()
        )
        assert row[0] == origin.model_dump(mode="json")
        assert row[1] == canonical_origin_hash(origin)
    assert error is not None
    assert "origin downgrade requires an empty origin table" in str(error)
