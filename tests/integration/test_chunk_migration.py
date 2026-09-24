import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from rag_access_guard_api.schemas.documents import DocumentSummary


def test_chunk_downgrade_refuses_data_loss(
    registered_document: DocumentSummary,
    auth_database: Engine,
) -> None:
    assert registered_document.active_version_id is not None
    with auth_database.connect() as connection:
        before = connection.execute(text("SELECT * FROM document_chunks")).all()
    with pytest.raises(DBAPIError, match="legacy-compatible"):
        command.downgrade(Config("apps/api/alembic.ini"), "0005_ingestion_manifest")
    with auth_database.connect() as connection:
        assert connection.execute(text("SELECT * FROM document_chunks")).all() == before
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "0006_document_chunks"
        )
