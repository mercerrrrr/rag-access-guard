"""DOCX-specific closed immutable recipe with historical format predicates preserved."""

from typing import Final

from rag_access_guard_api.persistence.embedding_manifest import EMBEDDING_MANIFEST
from rag_access_guard_api.persistence.pdf_manifest import PDF_PARSER

DOCX_OPTIONS_JSON: Final = (
    r'{"block_separator":"\n\n",'
    r'"cell_paragraph_separator":"\n",'
    r'"cell_separator":"\t",'
    r'"library_revision":"1.2.0",'
    r'"max_compression_ratio":100,'
    r'"max_entries":1000,'
    r'"max_entry_bytes":16777216,'
    r'"max_expanded_bytes":67108864,'
    r'"max_input_bytes":10485760,'
    r'"max_response_bytes":16842752,'
    r'"max_rss_bytes":536870912,'
    r'"max_text_bytes":8388608,'
    r'"newline":"LF",'
    r'"poll_ms":50,'
    r'"row_separator":"\n",'
    r'"timeout_seconds":30,'
    r'"unsupported_policy":"closed-body-paragraph-table-v1"}'
)
DOCX_MEDIA: Final = (
    "media_type IN ('text/plain', 'text/markdown', 'application/pdf', "
    "'application/vnd.openxmlformats-officedocument.wordprocessingml.document')"
)
DOCX_PARSER: Final = (
    PDF_PARSER
    + """ OR (media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    AND parser_revision='python-docx-body-v1:1.2.0'
    AND octet_length(extracted_text)<=8388608
    AND substring(original_bytes from 1 for 4)=decode('504b0304','hex'))"""
)
DOCX_MANIFEST: Final = (
    "(media_type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' AND "
    + EMBEDDING_MANIFEST.replace(
        '","parser_revision":"',
        '","parser_options":' + DOCX_OPTIONS_JSON.replace(":", r"\:") + ',"parser_revision":"',
    )
    + ")"
)
