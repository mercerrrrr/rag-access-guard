"""Bind append-only origin metadata to versions without rewriting historical manifests."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0013_document_origin"
down_revision = "0012_docx_ingestion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Only a version inserted in this transaction can acquire origin metadata."""
    _ = op.create_table(
        "document_origins",
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("origin", JSONB(), nullable=False),
        sa.Column("origin_sha256", sa.CHAR(64), nullable=False),
        sa.PrimaryKeyConstraint("version_id"),
        sa.ForeignKeyConstraint(["version_id"], ["document_versions.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("origin_sha256 ~ '^[0-9a-f]{64}$'", name="ck_document_origins_hash"),
        sa.CheckConstraint("jsonb_typeof(origin) = 'object'", name="ck_document_origins_object"),
    )
    op.execute("""CREATE FUNCTION guard_document_origin() RETURNS trigger AS $$
    BEGIN
        IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'document origin is immutable' USING ERRCODE = '23514';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM document_versions WHERE id = NEW.version_id
            AND status = 'stored'
            AND xmin::text::numeric = pg_current_xact_id()::text::numeric % 4294967296)
        THEN
            RAISE EXCEPTION 'origin requires a new version in the same transaction'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END $$ LANGUAGE plpgsql""")
    op.execute("""CREATE TRIGGER document_origins_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON document_origins
        FOR EACH ROW EXECUTE FUNCTION guard_document_origin()""")


def downgrade() -> None:
    """Refuse metadata loss; an empty disposable schema can be downgraded."""
    op.execute("LOCK TABLE document_origins IN ACCESS EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM document_origins) THEN
            RAISE EXCEPTION 'origin downgrade requires an empty origin table';
        END IF;
    END $$""")
    op.drop_table("document_origins")
    op.execute("DROP FUNCTION guard_document_origin()")
