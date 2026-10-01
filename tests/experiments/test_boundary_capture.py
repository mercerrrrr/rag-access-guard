from dataclasses import replace
from hashlib import sha256
from typing import Literal
from uuid import UUID

import pytest
from experiments.boundary_capture import CanonicalEvidence, capture_request, merge_user_origins
from experiments.input_boundary import observe_boundary

from rag_access_guard import CandidateChunk, PolicySnapshot, PreparedContext, SourceRef
from rag_access_guard.context import render_context


@pytest.fixture
def evidence() -> tuple[PreparedContext, CanonicalEvidence]:
    ref = SourceRef(document_id=UUID(int=1), document_version_id=UUID(int=2), chunk_id=UUID(int=3))
    digest = sha256(b"ALLOWED_CANONICAL").hexdigest()
    chunk = CandidateChunk(
        source_ref=ref, text="ALLOWED_CANONICAL", content_sha256=digest, token_count=1
    )
    prepared = PreparedContext(
        model_context=render_context((chunk,), ()),
        source_refs=(ref,),
        policy_revision=7,
        fingerprint="0" * 64,
    )
    snapshot = PolicySnapshot(
        principal_id=UUID(int=4),
        revision=7,
        allowed_refs=(ref,),
        denied_refs=(),
        provenance_valid=True,
        principal_active=True,
        thread_owned=True,
        canonical_chunk_hashes=((ref, digest),),
    )
    return prepared, CanonicalEvidence(snapshot=snapshot, history=())


def test_capture_requires_real_canonical_binding(
    evidence: tuple[PreparedContext, CanonicalEvidence],
) -> None:
    prepared, canonical = evidence
    request = capture_request(
        user_input="USER_MARKER",
        system_supplied_context=prepared.model_context,
        prepared=prepared,
        evidence=canonical,
        user_origin_markers=frozenset({"USER_MARKER"}),
    )
    assert request.provenance_valid
    assert not request.forbidden_system_refs
    assert request.user_input == "USER_MARKER"
    assert not observe_boundary(
        request, output="USER_MARKER", marker="USER_MARKER"
    ).system_violation


@pytest.mark.parametrize(
    "fault", ["wrong_text", "wrong_wire", "missing_refs", "unknown_hash", "invalid_snapshot"]
)
def test_capture_rejects_incomplete_or_swapped_binding(
    evidence: tuple[PreparedContext, CanonicalEvidence],
    fault: Literal["wrong_text", "wrong_wire", "missing_refs", "unknown_hash", "invalid_snapshot"],
) -> None:
    prepared, canonical = evidence
    context = prepared.model_context
    match fault:
        case "wrong_text":
            prepared = replace(
                prepared, model_context=context.replace("ALLOWED_CANONICAL", "FORGED")
            )
            context = prepared.model_context
        case "wrong_wire":
            context = "Unmanaged system text"
        case "missing_refs":
            prepared = replace(prepared, source_refs=())
        case "unknown_hash":
            canonical = replace(
                canonical, snapshot=replace(canonical.snapshot, canonical_chunk_hashes=())
            )
        case "invalid_snapshot":
            canonical = replace(
                canonical, snapshot=replace(canonical.snapshot, provenance_valid=False)
            )
    request = capture_request(
        user_input="",
        system_supplied_context=context,
        prepared=prepared,
        evidence=canonical,
        user_origin_markers=frozenset(),
    )
    assert not request.provenance_valid
    assert observe_boundary(request, output="Paraphrase", marker="FORGED").system_violation


def test_user_origin_survives_second_hop_history() -> None:
    first = merge_user_origins(
        user_input="Manual USER_MARKER",
        synthetic_markers=frozenset({"USER_MARKER"}),
        prior_origins=frozenset(),
    )
    second = merge_user_origins(
        user_input="Repeat", synthetic_markers=frozenset({"USER_MARKER"}), prior_origins=first
    )
    assert second == frozenset({"USER_MARKER"})


@pytest.mark.parametrize("reason", ["denied", "inactive", "foreign"])
def test_canonical_content_does_not_imply_authorization(
    evidence: tuple[PreparedContext, CanonicalEvidence],
    reason: str,
) -> None:
    prepared, canonical = evidence
    snapshot = replace(
        canonical.snapshot,
        allowed_refs=() if reason == "denied" else prepared.source_refs,
        denied_refs=prepared.source_refs if reason == "denied" else (),
        principal_active=reason != "inactive",
        thread_owned=reason != "foreign",
    )
    request = capture_request(
        user_input="",
        system_supplied_context=prepared.model_context,
        prepared=prepared,
        evidence=replace(canonical, snapshot=snapshot),
        user_origin_markers=frozenset(),
    )
    assert request.provenance_valid
    assert request.forbidden_system_refs == prepared.source_refs
    assert observe_boundary(
        request, output="Paraphrase", marker="ALLOWED_CANONICAL"
    ).system_violation
