"""Expand DOCX constraints without rewriting immutable source versions."""

import sqlalchemy as sa
from alembic import op

revision = "0012_docx_ingestion"
down_revision = "0011_history_closure"
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


DOCX_OPTIONS_PARTS = (
    r'{"block_separator":"\n\n",',
    r'"cell_paragraph_separator":"\n",',
    r'"cell_separator":"\t",',
    r'"library_revision":"1.2.0",',
    r'"max_compression_ratio":100,',
    r'"max_entries":1000,',
    r'"max_entry_bytes":16777216,',
    r'"max_expanded_bytes":67108864,',
    r'"max_input_bytes":10485760,',
    r'"max_response_bytes":16842752,',
    r'"max_rss_bytes":536870912,',
    r'"max_text_bytes":8388608,',
    r'"newline":"LF",',
    r'"poll_ms":50,',
    r'"row_separator":"\n",',
    r'"timeout_seconds":30,',
    r'"unsupported_policy":"closed-body-paragraph-table-v1"}',
)
DOCX_OPTIONS_JSON = "".join(DOCX_OPTIONS_PARTS)
DOCX_MANIFEST = (
    "(media_type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' AND "
    + MODEL_MANIFEST.replace(
        '","parser_revision":"',
        '","parser_options":' + DOCX_OPTIONS_JSON.replace(":", r"\:") + ',"parser_revision":"',
    )
    + ")"
)
DOCX_PARSER = (
    PDF_PARSER
    + """ OR (media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    AND parser_revision='python-docx-body-v1:1.2.0'
    AND octet_length(extracted_text)<=8388608
    AND substring(original_bytes from 1 for 4)=decode('504b0304','hex'))"""
)


def _replace(name: str, expression: str) -> None:
    op.drop_constraint(name, "document_versions", type_="check")
    op.create_check_constraint(name, "document_versions", expression)


def _resize_media_type(previous: int, current: int) -> None:
    # PostgreSQL requires detaching UPDATE OF dependencies for a column type change.
    op.execute("DROP TRIGGER document_versions_immutable ON document_versions")
    op.alter_column(
        "document_versions",
        "media_type",
        existing_type=sa.String(previous),
        type_=sa.String(current),
    )
    op.execute("""CREATE TRIGGER document_versions_immutable
        BEFORE DELETE OR UPDATE OF
        id, document_id, original_bytes, content_sha256, extracted_text, text_sha256,
        media_type, byte_size, parser_revision, created_at, created_by, ingestion_manifest
        ON document_versions FOR EACH ROW EXECUTE FUNCTION reject_document_version_mutation()""")


def upgrade() -> None:
    """Add a separate closed DOCX branch and preserve historical recipes."""
    _resize_media_type(64, 128)
    _replace(
        "ck_document_versions_media_type",
        """media_type IN ('text/plain','text/markdown','application/pdf',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document')""",
    )
    _replace("ck_document_versions_parser_revision", DOCX_PARSER)
    _replace(
        "ck_document_versions_manifest",
        "(media_type IN ('text/plain','text/markdown') AND ("
        + PREVIOUS_MANIFEST
        + ")) OR "
        + PDF_MANIFEST
        + " OR "
        + DOCX_MANIFEST,
    )


def downgrade() -> None:
    """Refuse data loss before shrinking MIME storage or historical predicates."""
    op.execute(
        sa.text("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM document_versions
            WHERE media_type =
                'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
            OR length(media_type)>64)
        THEN RAISE EXCEPTION 'DOCX downgrade requires compatible versions'; END IF;
        END $$""")
    )
    _replace(
        "ck_document_versions_manifest",
        "(media_type <> 'application/pdf' AND (" + PREVIOUS_MANIFEST + ")) OR " + PDF_MANIFEST,
    )
    _replace("ck_document_versions_parser_revision", PDF_PARSER)
    _replace(
        "ck_document_versions_media_type",
        "media_type IN ('text/plain','text/markdown','application/pdf')",
    )
    _resize_media_type(128, 64)
