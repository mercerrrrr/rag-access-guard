"""Accept bounded PDF artifacts without rewriting existing source versions."""

import sqlalchemy as sa
from alembic import op

revision = "0008_pdf_ingestion"
down_revision = "0007_chunk_embeddings"
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
PREVIOUS_MANIFEST = (
    "ingestion_manifest = "
    + LEGACY_MANIFEST
    + " OR ingestion_manifest = "
    + CHUNK_MANIFEST
    + " OR "
    + MODEL_MANIFEST
)
PDF_OPTIONS_JSON = (
    r'{"extraction_mode":"plain","max_input_bytes":10485760,"max_pages":200,'
    r'"max_response_bytes":16842752,"max_rss_bytes":536870912,"max_text_bytes":8388608,'
    r'"newline":"LF","page_separator":"\n\n","poll_ms":50,"strict":1,"timeout_seconds":30}'
)
PDF_MANIFEST = (
    "(media_type = 'application/pdf' AND "
    + MODEL_MANIFEST.replace(
        '","parser_revision":"',
        '","parser_options":' + PDF_OPTIONS_JSON.replace(":", r"\:") + ',"parser_revision":"',
    )
    + ")"
)
PDF_PARSER = """(media_type IN ('text/plain','text/markdown') AND parser_revision='utf8-text-v1')
    OR (media_type='application/pdf' AND parser_revision='pypdf-plain-v1:6.19.0'
        AND octet_length(extracted_text)<=8388608
        AND substring(original_bytes from 1 for 5)=decode('255044462d','hex'))"""


def _replace(name: str, expression: str) -> None:
    op.drop_constraint(name, "document_versions", type_="check")
    op.create_check_constraint(name, "document_versions", expression)


def upgrade() -> None:
    """Expand MIME, parser and recipe constraints; preserve immutable rows."""
    _replace(
        "ck_document_versions_media_type",
        "media_type IN ('text/plain','text/markdown','application/pdf')",
    )
    _replace("ck_document_versions_parser_revision", PDF_PARSER)
    _replace(
        "ck_document_versions_manifest",
        "(media_type <> 'application/pdf' AND (" + PREVIOUS_MANIFEST + ")) OR " + PDF_MANIFEST,
    )


def downgrade() -> None:
    """Refuse destructive downgrade while any PDF artifact remains."""
    op.execute(
        sa.text("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM document_versions WHERE media_type='application/pdf')
        THEN RAISE EXCEPTION 'PDF downgrade requires compatible versions'; END IF;
        END $$""")
    )
    _replace("ck_document_versions_manifest", PREVIOUS_MANIFEST)
    _replace("ck_document_versions_parser_revision", "parser_revision = 'utf8-text-v1'")
    _replace("ck_document_versions_media_type", "media_type IN ('text/plain', 'text/markdown')")
