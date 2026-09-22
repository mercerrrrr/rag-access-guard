"""Add immutable text versions and document-scoped active pointers."""

import sqlalchemy as sa
from alembic import op

revision = "0004_document_versions"
down_revision = "0003_auth_challenges"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Preserve legacy documents without inventing an active version."""
    op.create_table(
        "document_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("original_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("content_sha256", sa.CHAR(64), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=False),
        sa.Column("text_sha256", sa.CHAR(64), nullable=False),
        sa.Column("media_type", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("parser_revision", sa.String(100), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_document_versions"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_document_versions_document_id_documents",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_document_versions_created_by_users",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("document_id", "id", name="uq_document_versions_document_id_id"),
        sa.CheckConstraint(
            "byte_size > 0 AND byte_size <= 1048576 AND byte_size = octet_length(original_bytes)",
            name="ck_document_versions_byte_size",
        ),
        sa.CheckConstraint(
            """content_sha256 ~ '^[0-9a-f]{64}$'
            AND content_sha256 = encode(sha256(original_bytes), 'hex')""",
            name="ck_document_versions_content_hash",
        ),
        sa.CheckConstraint(
            """text_sha256 ~ '^[0-9a-f]{64}$'
            AND text_sha256 = encode(sha256(convert_to(extracted_text, 'UTF8')), 'hex')""",
            name="ck_document_versions_text_hash",
        ),
        sa.CheckConstraint(
            "extracted_text ~ '[^[:space:]]'", name="ck_document_versions_text_nonblank"
        ),
        sa.CheckConstraint("media_type = 'text/plain'", name="ck_document_versions_media_type"),
        sa.CheckConstraint(
            "parser_revision = 'utf8-text-v1'", name="ck_document_versions_parser_revision"
        ),
        sa.CheckConstraint("status = 'stored'", name="ck_document_versions_status"),
    )
    op.add_column("documents", sa.Column("active_version_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_documents_active_version",
        "documents",
        "document_versions",
        ["id", "active_version_id"],
        ["document_id", "id"],
        ondelete="RESTRICT",
    )
    op.execute("""CREATE FUNCTION reject_document_version_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
        RAISE EXCEPTION 'Document versions are immutable' USING ERRCODE = '23000';
        END $$""")
    op.execute("""CREATE TRIGGER document_versions_immutable BEFORE UPDATE OR DELETE
        ON document_versions FOR EACH ROW EXECUTE FUNCTION reject_document_version_mutation()""")


def downgrade() -> None:
    """Remove version storage; operators must back up content before downgrading."""
    op.drop_constraint("fk_documents_active_version", "documents", type_="foreignkey")
    op.drop_column("documents", "active_version_id")
    op.drop_table("document_versions")
    op.execute("DROP FUNCTION reject_document_version_mutation()")
