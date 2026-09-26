"""Integrity witness for the complete managed-source set of a stored answer."""

from hashlib import sha256

from rag_access_guard import SourceRef


def closure_digest(refs: tuple[SourceRef, ...]) -> bytes:
    """Hash distinct canonical tuples independently of display order."""
    rows = sorted({f"{r.document_id}/{r.document_version_id}/{r.chunk_id}" for r in refs})
    return sha256("\n".join(rows).encode("utf-8")).digest()


def closure_matches(refs: tuple[SourceRef, ...], expected: bytes | None) -> bool:
    """Missing or changed source sets are never accepted as complete provenance."""
    return bool(refs) and expected is not None and closure_digest(refs) == expected
