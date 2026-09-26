"""Shared validation of a policy adapter's exact source partition."""

import re

from rag_access_guard.types import PolicySnapshot, SourceRef


def valid_provenance(snapshot: PolicySnapshot, requested: tuple[SourceRef, ...]) -> bool:
    """Require disjoint coverage and one canonical hash per allowed source."""
    allowed = set(snapshot.allowed_refs)
    denied = set(snapshot.denied_refs)
    if (
        not snapshot.provenance_valid
        or len(allowed) != len(snapshot.allowed_refs)
        or len(denied) != len(snapshot.denied_refs)
        or allowed & denied
        or allowed | denied != set(requested)
    ):
        return False
    hashes = snapshot.canonical_chunk_hashes
    return (
        len(hashes) == len(allowed)
        and {ref for ref, _ in hashes} == allowed
        and all(re.fullmatch(r"[0-9a-f]{64}", digest) for _, digest in hashes)
    )
