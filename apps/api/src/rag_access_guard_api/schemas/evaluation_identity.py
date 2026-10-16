"""Authenticated scientific identity metadata without private paths or content."""

from typing import Literal
from uuid import UUID

from rag_access_guard_api.schemas.demo_corpus import CorpusValue, Sha256


class EvaluationMetadata(CorpusValue):
    """Actual server-bound generation and retrieval identities after readiness proof."""

    profile_hash: Sha256
    model_digest: Sha256
    renderer_identity: str
    retrieval_config_hash: Sha256
    corpus_sha256: Sha256


class EvaluationIdentity(EvaluationMetadata):
    """The final session-authorized policy and optional owned demo instance snapshot."""

    policy_revision: int
    demo_instance_id: UUID | None
    mode: Literal["local"] = "local"
    state: Literal["ready"] = "ready"
