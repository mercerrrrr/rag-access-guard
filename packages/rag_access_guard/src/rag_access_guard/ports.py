"""Host integration contracts without infrastructure dependencies."""

from typing import Protocol
from uuid import UUID

from rag_access_guard.types import PolicySnapshot, SourceRef


class PolicyReader(Protocol):
    """Resolve sources within one authenticated, consistent host policy view."""

    async def snapshot(
        self,
        principal_id: UUID,
        source_refs: tuple[SourceRef, ...],
        *,
        thread_id: UUID | None = None,
    ) -> PolicySnapshot:
        """Return the exact requested partition and canonical hashes."""
        ...


class TokenCounter(Protocol):
    """Count using the host's explicitly identified tokenizer configuration."""

    @property
    def identity(self) -> str:
        """Identify tokenizer revision and rendering configuration."""
        ...

    def count(self, text: str) -> int:
        """Return the token count for the complete supplied text."""
        ...
