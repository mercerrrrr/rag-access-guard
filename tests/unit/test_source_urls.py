from uuid import UUID

from rag_access_guard import SourceRef
from rag_access_guard_api.services.sources import build_source_url


def test_source_url_has_only_canonical_server_owned_components() -> None:
    ref = SourceRef(
        document_id=UUID("ABCDEFAB-1234-5678-9012-ABCDEFABCDEF"),
        document_version_id=UUID("ABCDEFAB-1234-5678-9013-ABCDEFABCDEF"),
        chunk_id=UUID("ABCDEFAB-1234-5678-9014-ABCDEFABCDEF"),
    )
    assert build_source_url(ref) == (
        "/api/documents/abcdefab-1234-5678-9012-abcdefabcdef/versions/"
        "abcdefab-1234-5678-9013-abcdefabcdef/content"
        "?chunk_id=abcdefab-1234-5678-9014-abcdefabcdef"
    )
