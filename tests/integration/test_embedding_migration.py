import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from rag_access_guard_api.schemas.documents import DocumentSummary


def test_embedding_downgrade_refuses_data_loss(
    registered_document: DocumentSummary,
    auth_database: Engine,
) -> None:
    assert registered_document.active_version_id is not None
    with auth_database.connect() as connection:
        before = connection.execute(
            text("SELECT chunk_id,embedding::text,model_id,model_revision FROM chunk_embeddings")
        ).all()
    with pytest.raises(DBAPIError, match="compatible versions"):
        command.downgrade(Config("apps/api/alembic.ini"), "0006_document_chunks")
    with auth_database.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT chunk_id,embedding::text,model_id,model_revision FROM chunk_embeddings"
                )
            ).all()
            == before
        )
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "0010_source_order"
        )
