"""Bind immutable text versions to their extraction manifest."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005_ingestion_manifest"
down_revision = "0004_document_versions"
branch_labels = None
depends_on = None

MANIFEST = """jsonb_build_object(
    'schema_version', 1, 'source_sha256', content_sha256, 'text_sha256', text_sha256,
    'byte_size', byte_size, 'parser_revision', parser_revision,
    'chunker_revision', NULL, 'tokenizer_revision', NULL,
    'embedding_model_id', NULL, 'embedding_model_revision', NULL,
    'config_sha256', encode(sha256(convert_to(
        '{"parser_revision":"' || parser_revision || '"}', 'UTF8')), 'hex'))"""


def upgrade() -> None:
    """Backfill from canonical columns without rewriting source data."""
    op.add_column("document_versions", sa.Column("ingestion_manifest", JSONB(), nullable=True))
    op.add_column("document_versions", sa.Column("failure_code", sa.String(32), nullable=True))
    op.execute("DROP TRIGGER document_versions_immutable ON document_versions")
    versions = sa.table("document_versions", sa.column("ingestion_manifest", JSONB()))
    op.execute(versions.update().values(ingestion_manifest=sa.literal_column(MANIFEST)))
    op.alter_column("document_versions", "ingestion_manifest", nullable=False)
    for name in ("byte_size", "media_type", "status"):
        op.drop_constraint("ck_document_versions_" + name, "document_versions", type_="check")
    op.create_check_constraint(
        "ck_document_versions_byte_size",
        "document_versions",
        "byte_size > 0 AND byte_size <= 10485760 AND byte_size = octet_length(original_bytes)",
    )
    op.create_check_constraint(
        "ck_document_versions_media_type",
        "document_versions",
        "media_type IN ('text/plain', 'text/markdown')",
    )
    op.create_check_constraint(
        "ck_document_versions_status",
        "document_versions",
        "status IN ('stored', 'chunked', 'indexing', 'ready', 'failed')",
    )
    op.create_check_constraint(
        "ck_document_versions_manifest",
        "document_versions",
        "ingestion_manifest = " + MANIFEST,
    )
    op.create_check_constraint(
        "ck_document_versions_failure",
        "document_versions",
        """(status = 'failed' AND failure_code IS NOT NULL AND failure_code IN (
            'unsupported_type', 'invalid_encoding', 'empty_text', 'size_limit',
            'parse_failed', 'index_failed'))
        OR (status <> 'failed' AND failure_code IS NULL)""",
    )
    op.execute("""CREATE TRIGGER document_versions_immutable BEFORE DELETE OR UPDATE OF
        id, document_id, original_bytes, content_sha256, extracted_text, text_sha256,
        media_type, byte_size, parser_revision, created_at, created_by, ingestion_manifest
        ON document_versions FOR EACH ROW EXECUTE FUNCTION reject_document_version_mutation()""")
    op.execute("""CREATE FUNCTION check_document_version_lifecycle() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
        IF TG_OP = 'INSERT' THEN
            IF NEW.status = 'stored' AND NEW.failure_code IS NULL THEN RETURN NEW; END IF;
        ELSIF (OLD.status = 'stored' AND NEW.status = 'chunked')
            OR (OLD.status = 'chunked' AND NEW.status = 'indexing')
            OR (OLD.status = 'indexing' AND NEW.status = 'ready')
            OR (OLD.status IN ('stored', 'chunked', 'indexing') AND NEW.status = 'failed') THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'Invalid document version lifecycle' USING ERRCODE = '23000';
        END $$""")
    op.execute("""CREATE TRIGGER document_versions_lifecycle BEFORE INSERT OR UPDATE OF
        status, failure_code ON document_versions FOR EACH ROW
        EXECUTE FUNCTION check_document_version_lifecycle()""")


def downgrade() -> None:
    """Refuse incompatible rows; never truncate or convert immutable content."""
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM document_versions WHERE byte_size > 1048576
            OR media_type <> 'text/plain' OR status <> 'stored' OR failure_code IS NOT NULL) THEN
            RAISE EXCEPTION 'Ingestion downgrade requires legacy-compatible versions';
        END IF;
        END $$""")
    op.execute("DROP TRIGGER document_versions_lifecycle ON document_versions")
    op.execute("DROP FUNCTION check_document_version_lifecycle()")
    op.execute("DROP TRIGGER document_versions_immutable ON document_versions")
    for name in ("byte_size", "media_type", "status", "manifest", "failure"):
        op.drop_constraint("ck_document_versions_" + name, "document_versions", type_="check")
    op.drop_column("document_versions", "failure_code")
    op.drop_column("document_versions", "ingestion_manifest")
    op.create_check_constraint(
        "ck_document_versions_byte_size",
        "document_versions",
        "byte_size > 0 AND byte_size <= 1048576 AND byte_size = octet_length(original_bytes)",
    )
    op.create_check_constraint(
        "ck_document_versions_media_type",
        "document_versions",
        "media_type = 'text/plain'",
    )
    op.create_check_constraint(
        "ck_document_versions_status",
        "document_versions",
        "status = 'stored'",
    )
    op.execute("""CREATE TRIGGER document_versions_immutable BEFORE UPDATE OR DELETE
        ON document_versions FOR EACH ROW EXECUTE FUNCTION reject_document_version_mutation()""")
