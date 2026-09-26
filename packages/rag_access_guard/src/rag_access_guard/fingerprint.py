"""Integrity binding for context, ordered provenance and tokenizer configuration."""

import json
from hashlib import sha256

from rag_access_guard.types import PreparedContext


def fingerprint(prepared: PreparedContext, identity: str, budget: int) -> str:
    """Hash an unambiguous versioned encoding; this is not a capability or signature."""
    encoded = json.dumps(
        [
            "rag-context-v1",
            identity,
            budget,
            prepared.policy_revision,
            prepared.model_context,
            [
                [str(r.document_id), str(r.document_version_id), str(r.chunk_id)]
                for r in prepared.source_refs
            ],
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return sha256(encoded.encode("utf-8")).hexdigest()
