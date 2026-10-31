from hashlib import sha256
from uuid import uuid4

import pytest

from rag_access_guard import CandidateChunk, PreparedContext, PriorTurn, SourceRef
from rag_access_guard.context import render_context
from rag_access_guard_api.services import generation_plan
from rag_access_guard_api.services.chat_state import Reservation
from rag_access_guard_api.services.origin_context import HostPrepared


@pytest.mark.parametrize("include_history", [False, True])
def test_replanning_never_reintroduces_an_unscored_pair_with_the_same_source(
    *, include_history: bool
) -> None:
    ref = SourceRef(document_id=uuid4(), document_version_id=uuid4(), chunk_id=uuid4())
    chunk = CandidateChunk(
        source_ref=ref,
        text="MANAGED",
        content_sha256=sha256(b"MANAGED").hexdigest(),
        token_count=1,
    )
    history = tuple(
        PriorTurn(
            turn_id=uuid4(),
            user_input=f"QUESTION-{index}",
            answer=f"ANSWER-{index}",
            source_refs=(ref,),
            provenance_complete=True,
        )
        for index in range(2)
    )
    selected = history[1:] if include_history else ()
    context = render_context((chunk,), selected)
    prepared = PreparedContext(
        model_context=context, source_refs=(ref,), policy_revision=0, fingerprint="0" * 64
    )
    request = generation_plan.PlanningInput(
        Reservation(uuid4(), uuid4(), uuid4(), uuid4(), 0),
        (chunk,),
        history,
        HostPrepared(prepared, context, "0" * 64, 5000),
        "QUESTION",
    )
    # The references alone cannot distinguish selected and previously pruned pairs.
    chunks, turns = generation_plan.evaluated_inputs(request)
    assert chunks == (chunk,)
    assert turns == selected
