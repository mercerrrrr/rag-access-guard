"""Host-owned annotations and budgets without changing standalone Guard encoding."""

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Final, assert_never

from rag_access_guard import (
    CandidateChunk,
    Guard,
    PreparedContext,
    PrepareDenied,
    PriorTurn,
    SourceRef,
    TokenCounter,
)
from rag_access_guard_api.adapters.model_tokens import ModelTokenCounter
from rag_access_guard_api.adapters.policy import PostgresPolicyReader
from rag_access_guard_api.config import Settings
from rag_access_guard_api.schemas.origin import DocumentOrigin
from rag_access_guard_api.services.model_profiles import get_model_profile
from rag_access_guard_api.services.origin import (
    InvalidOriginError,
    canonical_origin_hash,
    read_version_origin,
)
from rag_access_guard_api.services.security import ReadUoW

SYNTHETIC_LABEL: Final = "Учебный пример; вымышленный материал, не официальный документ."
OFFICIAL_LABEL: Final = (
    "Открытый источник; происхождение не подтверждает текущую юридическую действительность."
)
UPLOAD_LABEL: Final = "Пользовательский материал; достоверность издателя не подтверждена."
MAX_CONTEXT_TOKENS: Final = 5000


@dataclass(frozen=True, slots=True)
class OriginAnnotation:
    """One exact authorized tuple, its immutable origin digest and a closed label."""

    ref: SourceRef
    origin_sha256: str
    fixed_label: str


@dataclass(frozen=True, slots=True)
class HostPrepared:
    """Keep Guard's canonical context separate from the model's annotated request."""

    prepared: PreparedContext
    model_context: str
    origin_binding: str
    context_budget: int


def annotation(ref: SourceRef, origin: DocumentOrigin | None) -> OriginAnnotation:
    """Legacy null metadata has no label or annotation overhead."""
    if origin is None:
        return OriginAnnotation(ref, "", "")
    match origin.kind:
        case "synthetic_demo":
            label = SYNTHETIC_LABEL
        case "official_public":
            label = OFFICIAL_LABEL
        case "user_upload":
            label = UPLOAD_LABEL
        case _:
            assert_never(origin.kind)
    return OriginAnnotation(ref, canonical_origin_hash(origin), label)


def canonical_annotations(entries: tuple[OriginAnnotation, ...]) -> str:
    """Sort complete identities rather than display rank for the integrity binding."""
    return json.dumps(
        sorted(
            [
                [
                    str(e.ref.document_id),
                    str(e.ref.document_version_id),
                    str(e.ref.chunk_id),
                    e.origin_sha256,
                    e.fixed_label,
                ]
                for e in entries
            ]
        ),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def annotation_digest(entries: tuple[OriginAnnotation, ...]) -> str:
    """Bind null as well as annotated refs so deletions and substitutions differ."""
    return sha256(canonical_annotations(entries).encode("utf-8")).hexdigest()


def render_annotated(context: str, entries: tuple[OriginAnnotation, ...]) -> str:
    """No empty block: existing no-origin requests remain byte-for-byte identical."""
    active = tuple(e for e in entries if e.fixed_label)
    if not active:
        return context
    return context + "\norigin_annotations_v1:\n" + canonical_annotations(active)


async def selected_annotations(
    uow: ReadUoW, refs: tuple[SourceRef, ...]
) -> tuple[OriginAnnotation, ...]:
    """Call only after authorizing the exact selected refs in this protected UoW."""
    return tuple(
        [
            annotation(ref, await read_version_origin(uow.connection, ref.document_version_id))
            for ref in refs
        ]
    )


def request_tokens(counter: TokenCounter, *, user_input: str, context: str) -> tuple[int, int]:
    """Count the selected profile's full request and its reserved output."""
    match counter:
        case ModelTokenCounter():
            return counter.count_request(
                user_input=user_input, system_supplied_context=context
            ), counter.manifest.context_window - counter.manifest.max_output_tokens
        case _:
            profile = get_model_profile(Settings().model_profile)
            return counter.count(
                profile.render(user_input=user_input, system_supplied_context=context)
            ), profile.manifest.context_window - profile.manifest.max_output_tokens


async def prepare_origin_context(
    uow: ReadUoW,
    chunks: tuple[CandidateChunk, ...],
    history: tuple[PriorTurn, ...],
    counter: TokenCounter,
    *,
    user_input: str,
) -> HostPrepared | PrepareDenied | None:
    """Reserve candidate/history annotations, then check actual refs in at most two passes."""
    reader = PostgresPolicyReader(uow)
    refs = tuple(
        dict.fromkeys([c.source_ref for c in chunks] + [r for t in history for r in t.source_refs])
    )
    try:
        snapshot = await reader.snapshot(uow.principal.principal_id, refs)
    except Exception:  # noqa: BLE001 -- policy port boundary closes without adapter details.
        return PrepareDenied(reason="policy_unavailable", policy_revision=None)
    entries = await selected_annotations(uow, snapshot.allowed_refs)
    by_ref = {e.ref: e for e in entries}
    block = render_annotated("", entries)
    overhead = counter.count(block) if block else 0
    empty_request, request_limit = request_tokens(counter, user_input=user_input, context="")
    budget = min(MAX_CONTEXT_TOKENS, request_limit - empty_request) - overhead
    for _ in range(2):
        if budget <= 0:
            return None
        prepared = await Guard(counter, max_context_tokens=budget).prepare_context(
            uow.principal.principal_id, chunks, history, reader
        )
        match prepared:
            case PrepareDenied():
                return prepared
            case PreparedContext():
                actual = tuple(by_ref[r] for r in prepared.source_refs)
                context = render_annotated(prepared.model_context, actual)
                total, limit = request_tokens(counter, user_input=user_input, context=context)
                context_count = counter.count(context)
                if context_count <= MAX_CONTEXT_TOKENS and total <= limit:
                    return HostPrepared(prepared, context, annotation_digest(actual), budget)
                budget -= max(context_count - MAX_CONTEXT_TOKENS, total - limit, 1)
            case _:
                assert_never(prepared)
    return None


async def origin_binding_matches(
    uow: ReadUoW,
    prepared: PreparedContext,
    *,
    origin_binding: str | None,
    model_context: str | None,
) -> bool:
    """Recompute only already authorized refs; legacy attempts must have no origin metadata."""
    try:
        entries = await selected_annotations(uow, prepared.source_refs)
    except InvalidOriginError:
        return False
    if origin_binding is None:
        return not any(e.fixed_label for e in entries) and model_context is None
    return origin_binding == annotation_digest(entries) and model_context == render_annotated(
        prepared.model_context, entries
    )
