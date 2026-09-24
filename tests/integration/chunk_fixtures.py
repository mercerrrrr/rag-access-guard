from uuid import UUID

from sqlalchemy import Connection, text


def copy_stored_version(connection: Connection, source: UUID, target: UUID) -> None:
    _ = connection.execute(
        text("""INSERT INTO document_versions
        (id,document_id,original_bytes,content_sha256,extracted_text,text_sha256,
        media_type,byte_size,parser_revision,status,created_by,ingestion_manifest)
        SELECT :target,document_id,original_bytes,content_sha256,extracted_text,text_sha256,
        media_type,byte_size,parser_revision,'stored',created_by,ingestion_manifest
        FROM document_versions WHERE id=:source"""),
        {"target": target, "source": source},
    )


def copy_chunk_set(connection: Connection, source: UUID, target: UUID) -> None:
    _ = connection.execute(
        text("""INSERT INTO document_chunks
        (id,document_id,document_version_id,ordinal,text,content_sha256,
         token_count,char_start,char_end)
        SELECT gen_random_uuid(),document_id,:target,ordinal,text,content_sha256,
        token_count,char_start,char_end FROM document_chunks WHERE document_version_id=:source"""),
        {"source": source, "target": target},
    )


def copy_chunked_version(connection: Connection, source: UUID, target: UUID) -> None:
    copy_stored_version(connection, source, target)
    copy_chunk_set(connection, source, target)
    _ = connection.execute(
        text("UPDATE document_versions SET status='chunked' WHERE id=:id"), {"id": target}
    )
