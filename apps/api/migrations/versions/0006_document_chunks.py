"""Store immutable canonical chunks without rewriting legacy versions."""

import sqlalchemy as sa
from alembic import op

revision = "0006_document_chunks"
down_revision = "0005_ingestion_manifest"
branch_labels = None
depends_on = None

LEGACY_MANIFEST = """jsonb_build_object(
            'schema_version', 1, 'source_sha256', content_sha256, 'text_sha256', text_sha256,
            'byte_size', byte_size, 'parser_revision', parser_revision,
            'chunker_revision', NULL, 'tokenizer_revision', NULL,
            'embedding_model_id', NULL, 'embedding_model_revision', NULL,
            'config_sha256', encode(sha256(convert_to(
                '{"parser_revision":"' || parser_revision || '"}', 'UTF8')), 'hex'))"""
CHUNK_MANIFEST = """jsonb_build_object(
            'schema_version', 1, 'source_sha256', content_sha256, 'text_sha256', text_sha256,
            'byte_size', byte_size, 'parser_revision', parser_revision,
            'chunker_revision', 'e5-window400-overlap50-offsets-v1',
            'tokenizer_revision', 'intfloat/multilingual-e5-small@'
                || '614241f622f53c4eeff9890bdc4f31cfecc418b3:content-no-special',
            'embedding_model_id', NULL, 'embedding_model_revision', NULL,
            'config_sha256', encode(sha256(convert_to(
                '{"chunker_revision":"e5-window400-overlap50-offsets-v1","parser_revision":"'
                || parser_revision || '","tokenizer_revision":"intfloat/multilingual-e5-small@'
                || '614241f622f53c4eeff9890bdc4f31cfecc418b3:content-no-special"}',
                'UTF8')), 'hex'))"""


def upgrade() -> None:
    """Admit pinned manifests and seal chunk sets at the lifecycle boundary."""
    op.drop_constraint("ck_document_versions_manifest", "document_versions", type_="check")
    op.create_check_constraint(
        "ck_document_versions_manifest",
        "document_versions",
        "ingestion_manifest = " + LEGACY_MANIFEST + " OR ingestion_manifest = " + CHUNK_MANIFEST,
    )
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.CHAR(64), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "document_version_id", "ordinal", name="uq_document_chunks_version_ordinal"
        ),
        sa.UniqueConstraint(
            "document_id", "document_version_id", "id", name="uq_document_chunks_source"
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "document_version_id"],
            ["document_versions.document_id", "document_versions.id"],
            name="fk_document_chunks_version",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_document_chunks_ordinal"),
        sa.CheckConstraint("token_count BETWEEN 1 AND 400", name="ck_document_chunks_tokens"),
        sa.CheckConstraint(
            """char_start >= 0 AND char_end > char_start
            AND char_end - char_start = char_length(text)""",
            name="ck_document_chunks_offsets",
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$' "
            "AND content_sha256 = encode(sha256(convert_to(text, 'UTF8')), 'hex')",
            name="ck_document_chunks_hash",
        ),
    )
    op.execute("""CREATE FUNCTION check_document_chunk_insert() RETURNS trigger
        LANGUAGE plpgsql AS $$ DECLARE source RECORD; BEGIN
        SELECT status, byte_size, ingestion_manifest INTO source FROM document_versions
        WHERE id = NEW.document_version_id AND document_id = NEW.document_id FOR UPDATE;
        IF NOT FOUND OR source.status <> 'stored'
            OR source.ingestion_manifest->>'chunker_revision' IS NULL
            OR NEW.char_end > source.byte_size THEN
            RAISE EXCEPTION 'Invalid canonical chunk' USING ERRCODE = '23000';
        END IF;
        RETURN NEW;
        END $$""")
    op.execute("""CREATE TRIGGER document_chunks_canonical BEFORE INSERT ON document_chunks
        FOR EACH ROW EXECUTE FUNCTION check_document_chunk_insert()""")
    op.execute("""CREATE TRIGGER document_chunks_immutable
        BEFORE UPDATE OR DELETE ON document_chunks
        FOR EACH ROW EXECUTE FUNCTION reject_document_version_mutation()""")
    op.execute("""CREATE FUNCTION check_complete_chunk_set() RETURNS trigger
        LANGUAGE plpgsql AS $$ DECLARE canonical TEXT; consistent_overlap BOOLEAN; BEGIN
        IF NEW.status = 'chunked' AND (
            NEW.ingestion_manifest->>'chunker_revision' IS NULL OR NOT EXISTS (
                SELECT 1 FROM document_chunks WHERE document_version_id = NEW.id
                GROUP BY document_version_id HAVING min(ordinal) = 0
                AND max(ordinal) + 1 = count(*) AND min(char_start) = 0
                AND max(char_end) = char_length(NEW.extracted_text)
            ) OR EXISTS (
                SELECT 1 FROM (
                    SELECT char_start, char_end,
                        lag(char_end) OVER (ORDER BY ordinal) AS previous_end
                    FROM document_chunks WHERE document_version_id = NEW.id
                ) AS windows WHERE previous_end IS NOT NULL
                    AND (char_start > previous_end OR char_end <= previous_end)
            )
        ) THEN
            RAISE EXCEPTION 'Incomplete canonical chunk set' USING ERRCODE = '23000';
        END IF;
        IF NEW.status = 'chunked' THEN
            WITH windows AS (
                SELECT *, lag(char_start) OVER (ORDER BY ordinal) AS previous_start,
                    lag(char_end) OVER (ORDER BY ordinal) AS previous_end,
                    lag(text) OVER (ORDER BY ordinal) AS previous_text
                FROM document_chunks WHERE document_version_id = NEW.id
            ) SELECT string_agg(CASE WHEN ordinal = 0 THEN text ELSE
                    substring(text FROM previous_end - char_start + 1) END, '' ORDER BY ordinal),
                bool_and(ordinal = 0 OR (char_start >= previous_start AND
                    substring(text FROM 1 FOR previous_end - char_start)
                    = right(previous_text, previous_end - char_start)))
            INTO canonical, consistent_overlap FROM windows;
            IF canonical IS DISTINCT FROM NEW.extracted_text
                OR consistent_overlap IS NOT TRUE THEN
                RAISE EXCEPTION 'Invalid canonical chunk set' USING ERRCODE = '23000';
            END IF;
        END IF;
        RETURN NEW;
        END $$""")
    op.execute("""CREATE TRIGGER document_versions_chunks
        BEFORE UPDATE OF status ON document_versions
        FOR EACH ROW EXECUTE FUNCTION check_complete_chunk_set()""")


def downgrade() -> None:
    """Refuse loss of chunks or manifests that the previous schema cannot represent."""
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM document_chunks) OR EXISTS (
            SELECT 1 FROM document_versions
            WHERE ingestion_manifest->>'chunker_revision' IS NOT NULL
        ) THEN RAISE EXCEPTION 'Chunk downgrade requires legacy-compatible versions'; END IF;
        END $$""")
    op.execute("DROP TRIGGER document_versions_chunks ON document_versions")
    op.execute("DROP FUNCTION check_complete_chunk_set()")
    op.drop_table("document_chunks")
    op.execute("DROP FUNCTION check_document_chunk_insert()")
    op.drop_constraint("ck_document_versions_manifest", "document_versions", type_="check")
    op.create_check_constraint(
        "ck_document_versions_manifest",
        "document_versions",
        "ingestion_manifest = " + LEGACY_MANIFEST,
    )
