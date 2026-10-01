"""Observed protected reads, separate from experiment generation and release."""

from __future__ import annotations

from time import perf_counter
from typing import TYPE_CHECKING

from experiments.baseline_reads import baseline_source, baseline_thread
from experiments.observations import Observation
from rag_access_guard import SourceRef
from rag_access_guard_api.adapters.policy import PostgresPolicyReader
from rag_access_guard_api.schemas.access import AccessibleDocuments
from rag_access_guard_api.schemas.chat import AvailableTurn
from rag_access_guard_api.schemas.sources import SourceNotFound
from rag_access_guard_api.services.access import list_accessible_documents
from rag_access_guard_api.services.chat_read import read_thread
from rag_access_guard_api.services.sources import read_source

if TYPE_CHECKING:
    from experiments.host_state import HostState
    from experiments.scenario_types import Principal
    from rag_access_guard_api.schemas.chat import ThreadDetail
    from rag_access_guard_api.schemas.sources import SourceContent


async def stored_read(host: HostState, action_id: str, reader: Principal) -> ThreadDetail:
    """Observe the actual protected read projection without changing stored rows."""
    if host.thread_id is None:
        message = "No experiment thread"
        raise RuntimeError(message)
    started = perf_counter()
    actor = host.corpus.actors[reader]
    async with host.policy.protected_read(actor.token) as uow:
        detail = (
            await read_thread(uow, host.thread_id, session_token=actor.token)
            if host.arm == "guarded"
            else await baseline_thread(uow, host.thread_id, session_token=actor.token)
        )
        visible = tuple(t for t in detail.turns if isinstance(t, AvailableTurn))
        refs = tuple(
            dict.fromkeys(
                SourceRef(
                    document_id=s.document_id,
                    document_version_id=s.document_version_id,
                    chunk_id=s.chunk_id,
                )
                for t in visible
                for s in t.sources
            )
        )
        snapshot = await PostgresPolicyReader(uow).snapshot(actor.user_id, refs)
    host.observations.append(
        Observation(
            action_id=action_id,
            surface="stored_read",
            source_refs=refs,
            forbidden_refs=snapshot.denied_refs,
            provenance_valid=snapshot.provenance_valid,
            elapsed_ms=(perf_counter() - started) * 1000,
            body="\n".join(t.answer for t in visible),
            titles=tuple(s.title for t in visible for s in t.sources),
        )
    )
    return detail


async def document_list(host: HostState, action_id: str) -> AccessibleDocuments:
    """Observe only metadata returned by the current access-filtered SQL predicate."""
    started = perf_counter()
    async with host.policy.protected_read(host.actor.token) as uow:
        items = await list_accessible_documents(uow)
    host.observations.append(
        Observation(
            action_id=action_id,
            surface="document_list",
            source_refs=(),
            forbidden_refs=(),
            provenance_valid=True,
            elapsed_ms=(perf_counter() - started) * 1000,
            titles=tuple(item.title for item in items),
        )
    )
    return AccessibleDocuments(items=items)


async def source_read(host: HostState, action_id: str, ref: SourceRef) -> SourceContent | None:
    """Observe source content or indistinguishable denial through the real service."""
    started = perf_counter()
    async with host.policy.protected_read(host.actor.token) as uow:
        snapshot = await PostgresPolicyReader(uow).snapshot(host.actor.user_id, (ref,))
        try:
            source = (
                await read_source(uow, ref)
                if host.arm == "guarded"
                else await baseline_source(uow, ref)
            )
        except SourceNotFound:
            source = None
    host.observations.append(
        Observation(
            action_id=action_id,
            surface="source_read",
            source_refs=(ref,) if source else (),
            forbidden_refs=snapshot.denied_refs if source else (),
            provenance_valid=snapshot.provenance_valid,
            elapsed_ms=(perf_counter() - started) * 1000,
            body=source.text if source else "",
            titles=(source.title,) if source else (),
        )
    )
    return source
