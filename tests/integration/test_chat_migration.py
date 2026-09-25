from concurrent.futures import ThreadPoolExecutor
from time import monotonic

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from tests.support.chat import seed_thread


@pytest.mark.usefixtures("registered_document")
def test_chat_migration_preserves_documents_and_refuses_history_loss(
    admin_client: TestClient,
    auth_database: Engine,
) -> None:
    config = Config("apps/api/alembic.ini")
    with auth_database.connect() as connection:
        before = connection.execute(text("SELECT * FROM document_versions")).one()
    command.downgrade(config, "0008_pdf_ingestion")
    command.upgrade(config, "head")
    command.check(config)
    with auth_database.connect() as connection:
        assert connection.execute(text("SELECT * FROM document_versions")).one() == before
    created = admin_client.post(
        "/api/chat/threads",
        json={},
        headers={
            "Origin": "https://rag.test",
            "X-CSRF-Token": admin_client.cookies["__Host-rag_csrf"],
        },
    )
    assert created.status_code == 201
    with pytest.raises(DBAPIError, match="Chat downgrade requires empty conversations"):
        command.downgrade(config, "0008_pdf_ingestion")
    with auth_database.connect() as connection:
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "0009_chat_threads"
        )
        assert connection.execute(text("SELECT count(*) FROM chat_threads")).scalar_one() == 1


def test_downgrade_cannot_discard_concurrently_created_thread(auth_database: Engine) -> None:
    with auth_database.connect() as writer, ThreadPoolExecutor(max_workers=1) as executor:
        _ = seed_thread(writer)
        backend = TypeAdapter(int).validate_python(
            writer.execute(text("SELECT pg_backend_pid()")).scalar_one()
        )
        downgrade = executor.submit(
            command.downgrade, Config("apps/api/alembic.ini"), "0008_pdf_ingestion"
        )
        try:
            deadline = monotonic() + 10
            with auth_database.connect() as observer:
                while not observer.execute(
                    text("""SELECT EXISTS(SELECT 1 FROM pg_stat_activity
                    WHERE datname=current_database() AND :blocker=ANY(pg_blocking_pids(pid))
                    AND wait_event_type='Lock')"""),
                    {"blocker": backend},
                ).scalar_one():
                    assert monotonic() < deadline
                    observer.rollback()
        finally:
            writer.commit()
        with pytest.raises(DBAPIError, match="Chat downgrade requires empty conversations"):
            downgrade.result(10)
    with auth_database.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM chat_threads")).scalar_one() == 1
