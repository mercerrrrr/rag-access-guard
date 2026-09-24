"""Bind immutable normalized vectors to complete model-version artifacts."""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "0007_chunk_embeddings"
down_revision = "0006_document_chunks"
branch_labels = None
depends_on = None

LEGACY_MANIFEST = """jsonb_build_object(
    'schema_version',1,'source_sha256',content_sha256,'text_sha256',text_sha256,
    'byte_size',byte_size,'parser_revision',parser_revision,
    'chunker_revision',NULL,'tokenizer_revision',NULL,
    'embedding_model_id',NULL,'embedding_model_revision',NULL,
    'config_sha256',encode(sha256(convert_to(
        '{"parser_revision":"' || parser_revision || '"}','UTF8')),'hex'))"""
CHUNK_MANIFEST = """jsonb_build_object(
    'schema_version',1,'source_sha256',content_sha256,'text_sha256',text_sha256,
    'byte_size',byte_size,'parser_revision',parser_revision,
    'chunker_revision','e5-window400-overlap50-offsets-v1',
    'tokenizer_revision','intfloat/multilingual-e5-small@'
        || '614241f622f53c4eeff9890bdc4f31cfecc418b3:content-no-special',
    'embedding_model_id',NULL,'embedding_model_revision',NULL,
    'config_sha256',encode(sha256(convert_to(
        '{"chunker_revision":"e5-window400-overlap50-offsets-v1","parser_revision":"'
        || parser_revision || '","tokenizer_revision":"intfloat/multilingual-e5-small@'
        || '614241f622f53c4eeff9890bdc4f31cfecc418b3:content-no-special"}',
        'UTF8')),'hex'))"""
PREVIOUS_MANIFEST = (
    "ingestion_manifest = " + LEGACY_MANIFEST + " OR ingestion_manifest = " + CHUNK_MANIFEST
)

MODEL_MANIFEST = """(
    ingestion_manifest->>'embedding_model_id' = 'intfloat/multilingual-e5-small'
    AND ingestion_manifest->>'embedding_model_revision' ~ '^[0-9a-f]{40}$'
    AND ingestion_manifest = jsonb_build_object(
    'schema_version',1,'source_sha256',content_sha256,'text_sha256',text_sha256,
    'byte_size',byte_size,'parser_revision',parser_revision,
    'chunker_revision','e5-window400-overlap50-offsets-v1',
    'tokenizer_revision','intfloat/multilingual-e5-small@'
        || '614241f622f53c4eeff9890bdc4f31cfecc418b3:content-no-special',
    'embedding_model_id','intfloat/multilingual-e5-small',
    'embedding_model_revision',ingestion_manifest->>'embedding_model_revision',
    'config_sha256',encode(sha256(convert_to(
        '{"chunker_revision":"e5-window400-overlap50-offsets-v1",'
        || '"embedding_model_id":"intfloat/multilingual-e5-small","embedding_model_revision":"'
        || (ingestion_manifest->>'embedding_model_revision') || '","parser_revision":"'
        || parser_revision || '","tokenizer_revision":"intfloat/multilingual-e5-small@'
        || '614241f622f53c4eeff9890bdc4f31cfecc418b3:content-no-special"}',
        'UTF8')),'hex'))
) IS TRUE"""


def upgrade() -> None:
    """Expand legacy manifests without rewriting existing versions or chunks."""
    op.drop_constraint("ck_document_versions_manifest", "document_versions", type_="check")
    op.create_check_constraint(
        "ck_document_versions_manifest",
        "document_versions",
        PREVIOUS_MANIFEST + " OR " + MODEL_MANIFEST,
    )
    op.create_table(
        "chunk_embeddings",
        sa.Column(
            "chunk_id",
            sa.Uuid(),
            sa.ForeignKey("document_chunks.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.Column("model_id", sa.String(200), nullable=False),
        sa.Column("model_revision", sa.CHAR(40), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.CheckConstraint(
            "abs(vector_norm(embedding) - 1) <= 0.00001", name="ck_chunk_embeddings_norm"
        ),
        sa.CheckConstraint(
            "model_revision ~ '^[0-9a-f]{40}$'", name="ck_chunk_embeddings_revision"
        ),
    )
    op.execute("""CREATE FUNCTION check_chunk_embedding_insert() RETURNS trigger
        LANGUAGE plpgsql AS $$ DECLARE source RECORD; BEGIN
        SELECT v.status,v.ingestion_manifest INTO source FROM document_versions v
        JOIN document_chunks c ON c.document_version_id=v.id WHERE c.id=NEW.chunk_id
        FOR UPDATE OF v;
        IF NOT FOUND OR source.status <> 'indexing'
            OR NEW.model_id IS DISTINCT FROM source.ingestion_manifest->>'embedding_model_id'
            OR NEW.model_revision IS DISTINCT FROM
                source.ingestion_manifest->>'embedding_model_revision'
        THEN RAISE EXCEPTION 'Invalid embedding provenance' USING ERRCODE='23000'; END IF;
        RETURN NEW; END $$""")
    op.execute("""CREATE TRIGGER chunk_embeddings_canonical BEFORE INSERT ON chunk_embeddings
        FOR EACH ROW EXECUTE FUNCTION check_chunk_embedding_insert()""")
    op.execute("""CREATE TRIGGER chunk_embeddings_immutable
        BEFORE UPDATE OR DELETE ON chunk_embeddings
        FOR EACH ROW EXECUTE FUNCTION reject_document_version_mutation()""")
    op.execute("""CREATE FUNCTION check_complete_embedding_set() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
        IF NEW.status='ready' AND (
            NEW.ingestion_manifest->>'embedding_model_id' IS NULL
            OR NOT EXISTS (SELECT 1 FROM document_chunks WHERE document_version_id=NEW.id)
            OR EXISTS (SELECT 1 FROM document_chunks c LEFT JOIN chunk_embeddings e
                ON e.chunk_id=c.id WHERE c.document_version_id=NEW.id AND e.chunk_id IS NULL)
        ) THEN RAISE EXCEPTION 'Incomplete embedding set' USING ERRCODE='23000'; END IF;
        RETURN NEW; END $$""")
    op.execute("""CREATE TRIGGER document_versions_embeddings
        BEFORE UPDATE OF status ON document_versions
        FOR EACH ROW EXECUTE FUNCTION check_complete_embedding_set()""")


def downgrade() -> None:
    """Refuse removal of model-bound artifacts; preserve all prior manifest rules."""
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM chunk_embeddings) OR EXISTS (SELECT 1 FROM document_versions
            WHERE ingestion_manifest->>'embedding_model_id' IS NOT NULL)
        THEN RAISE EXCEPTION 'Embedding downgrade requires compatible versions'; END IF;
        END $$""")
    op.execute("DROP TRIGGER document_versions_embeddings ON document_versions")
    op.execute("DROP FUNCTION check_complete_embedding_set()")
    op.drop_table("chunk_embeddings")
    op.execute("DROP FUNCTION check_chunk_embedding_insert()")
    op.drop_constraint("ck_document_versions_manifest", "document_versions", type_="check")
    op.create_check_constraint(
        "ck_document_versions_manifest", "document_versions", PREVIOUS_MANIFEST
    )
