"""Database records and complete migration metadata."""

from rag_access_guard_api.persistence.auth_challenges import AuthChallenge
from rag_access_guard_api.persistence.auth_limits import AuthRateBucket
from rag_access_guard_api.persistence.base import Base
from rag_access_guard_api.persistence.chunks import DocumentChunk
from rag_access_guard_api.persistence.documents import Document, DocumentGrant
from rag_access_guard_api.persistence.embeddings import ChunkEmbedding
from rag_access_guard_api.persistence.identity import Role, User, UserRole
from rag_access_guard_api.persistence.policy import AuditEvent, PolicyState
from rag_access_guard_api.persistence.session import Session
from rag_access_guard_api.persistence.versions import DocumentVersion

__all__ = [
    "AuditEvent",
    "AuthChallenge",
    "AuthRateBucket",
    "Base",
    "ChunkEmbedding",
    "Document",
    "DocumentChunk",
    "DocumentGrant",
    "DocumentVersion",
    "PolicyState",
    "Role",
    "Session",
    "User",
    "UserRole",
]
