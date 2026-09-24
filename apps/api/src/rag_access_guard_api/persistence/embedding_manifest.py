"""Manifest predicate for model-bound versions; legacy predicates remain unchanged."""

from typing import Final

EMBEDDING_MANIFEST: Final = """(
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
