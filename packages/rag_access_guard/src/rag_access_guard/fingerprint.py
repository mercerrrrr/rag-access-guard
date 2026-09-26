"""Integrity binding for context, ordered provenance and tokenizer configuration."""

import json
from hashlib import sha256

from rag_access_guard.context import RENDERER_REVISION
from rag_access_guard.types import PreparedContext


def fingerprint(
    prepared: PreparedContext, identity: str, budget: int, *, max_prior_turns: int
) -> str:
    """Hash an unambiguous versioned encoding; this is not a capability or signature."""
    encoded = json.dumps(
        {
            "schema": "rag-access-guard/prepared/v1",
            "tokenizer_identity": identity,
            "max_context_tokens": budget,
            "max_prior_turns": max_prior_turns,
            "renderer_revision": RENDERER_REVISION,
            "policy_revision": prepared.policy_revision,
            "model_context": prepared.model_context,
            "source_refs": [
                [str(r.document_id), str(r.document_version_id), str(r.chunk_id)]
                for r in prepared.source_refs
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(encoded.encode("utf-8")).hexdigest()
