"""PDF-specific immutable recipe, alongside unchanged historical text recipes."""

from typing import Final

from rag_access_guard_api.persistence.embedding_manifest import EMBEDDING_MANIFEST

PDF_OPTIONS_JSON: Final = (
    r'{"extraction_mode":"plain","max_input_bytes":10485760,"max_pages":200,'
    r'"max_response_bytes":16842752,"max_rss_bytes":536870912,"max_text_bytes":8388608,'
    r'"newline":"LF","page_separator":"\n\n","poll_ms":50,"strict":1,"timeout_seconds":30}'
)
PDF_MANIFEST: Final = (
    "(media_type = 'application/pdf' AND "
    + EMBEDDING_MANIFEST.replace(
        '","parser_revision":"',
        '","parser_options":' + PDF_OPTIONS_JSON.replace(":", r"\:") + ',"parser_revision":"',
    )
    + ")"
)
PDF_MEDIA: Final = "media_type IN ('text/plain', 'text/markdown', 'application/pdf')"
PDF_PARSER: Final = """(media_type IN ('text/plain','text/markdown')
    AND parser_revision='utf8-text-v1')
    OR (media_type='application/pdf' AND parser_revision='pypdf-plain-v1:6.19.0'
        AND octet_length(extracted_text)<=8388608
        AND substring(original_bytes from 1 for 5)=decode('255044462d','hex'))"""
