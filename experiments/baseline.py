"""Experimental omission of repeated source-policy gates, never of host integrity."""

from dataclasses import replace

from pydantic import ValidationError

from experiments.comparison_config import ComparisonConfig
from rag_access_guard import (
    CandidateChunk,
    Guard,
    PolicyReader,
    PreparedContext,
    PrepareDenied,
    PriorTurn,
    SourceRef,
)
from rag_access_guard.context import bound_context, matches_canonical, matches_history
from rag_access_guard.fingerprint import fingerprint
from rag_access_guard_api.adapters.canonical import CanonicalChunk, canonical_query
from rag_access_guard_api.adapters.llm import FakeTokenCounter
from rag_access_guard_api.adapters.policy import PostgresPolicyReader
from rag_access_guard_api.persistence import DocumentChunk
from rag_access_guard_api.schemas.search import InvalidProvenanceError
from rag_access_guard_api.services.security import ReadUoW


async def canonical_hashes(
    uow: ReadUoW, refs: tuple[SourceRef, ...]
) -> tuple[tuple[SourceRef, str], ...]:
    """Load immutable content evidence separately from current source authorization."""
    hashes: list[tuple[SourceRef, str]] = []
    for ref in refs:
        row = (
            (
                await uow.connection.execute(
                    canonical_query().where(
                        DocumentChunk.id == ref.chunk_id,
                        DocumentChunk.document_id == ref.document_id,
                        DocumentChunk.document_version_id == ref.document_version_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is not None:
            try:
                chunk = CanonicalChunk.model_validate(row).candidate()
            except (InvalidProvenanceError, ValidationError):
                continue
            hashes.append((ref, chunk.content_sha256))
    return tuple(hashes)


async def prepare_baseline(
    uow: ReadUoW,
    chunks: tuple[CandidateChunk, ...],
    history: tuple[PriorTurn, ...],
    config: ComparisonConfig,
    reader: PolicyReader,
) -> PreparedContext | PrepareDenied:
    """Keep new-candidate ACL and canonical history integrity, omitting only history ACL."""
    counter = FakeTokenCounter()
    fresh = await Guard(counter, config.max_context_tokens, config.max_prior_turns).prepare_context(
        uow.principal.principal_id, chunks, (), reader
    )
    if isinstance(fresh, PrepareDenied):
        return fresh
    verified: list[PriorTurn] = []
    window = history[-config.max_prior_turns :] if config.max_prior_turns else ()
    for turn in window:
        hashes = await canonical_hashes(uow, turn.source_refs)
        if (
            turn.provenance_complete
            and turn.source_refs
            and turn.answer.strip()
            and "\x00" not in turn.answer
            and len(hashes) == len(set(turn.source_refs))
        ):
            verified.append(turn)
    bounded = bound_context(
        tuple(chunk for chunk in chunks if chunk.source_ref in fresh.source_refs),
        tuple(verified),
        token_counter=counter,
        max_context_tokens=config.max_context_tokens,
        max_prior_turns=config.max_prior_turns,
    )
    prepared = PreparedContext(
        model_context=bounded.model_context,
        source_refs=bounded.source_refs,
        policy_revision=fresh.policy_revision,
        fingerprint="0" * 64,
    )
    return replace(
        prepared,
        fingerprint=fingerprint(
            prepared,
            counter.identity,
            config.max_context_tokens,
            max_prior_turns=config.max_prior_turns,
        ),
    )


async def baseline_integrity(
    uow: ReadUoW,
    prepared: PreparedContext,
    history: tuple[PriorTurn, ...],
    config: ComparisonConfig,
) -> bool:
    """Validate exact bytes, closure, history and fingerprint without granting source rights."""
    snapshot = await PostgresPolicyReader(uow).snapshot(
        uow.principal.principal_id, prepared.source_refs
    )
    hashes = await canonical_hashes(uow, prepared.source_refs)
    evidence = replace(snapshot, canonical_chunk_hashes=hashes)
    return (
        snapshot.provenance_valid
        and len(hashes) == len(set(prepared.source_refs))
        and matches_canonical(prepared, evidence)
        and matches_history(prepared, history)
        and prepared.fingerprint
        == fingerprint(
            prepared,
            FakeTokenCounter().identity,
            config.max_context_tokens,
            max_prior_turns=config.max_prior_turns,
        )
    )
