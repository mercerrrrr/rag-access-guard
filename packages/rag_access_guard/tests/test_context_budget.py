from dataclasses import replace
from hashlib import sha256
from uuid import UUID

import pytest
from packages.rag_access_guard.tests.test_history import Counter, ref

from rag_access_guard import CandidateChunk, PriorTurn
from rag_access_guard.context import bound_context, render_context


def candidate(text: str, number: int = 1) -> CandidateChunk:
    return CandidateChunk(
        source_ref=ref(number),
        text=text,
        content_sha256=sha256(text.encode()).hexdigest(),
        token_count=1,
    )


def turn(number: int) -> PriorTurn:
    return PriorTurn(
        turn_id=UUID(int=100 + number),
        user_input=f"QUESTION_{number}",
        answer=f"ANSWER_{number}",
        source_refs=(ref(number),),
        provenance_complete=True,
    )


@pytest.mark.parametrize("size", [5000, 5001])
def test_exact_serialized_boundary_keeps_or_drops_whole_chunk(size: int) -> None:
    overhead = len(render_context((candidate(""),), ()))
    chunk = candidate("x" * (size - overhead))
    assert len(render_context((chunk,), ())) == size
    result = bound_context((chunk,), (), token_counter=Counter())
    if size == 5000:
        assert result.model_context == render_context((chunk,), ())
        assert result.source_refs == (chunk.source_ref,)
    else:
        assert result.model_context == ""
        assert result.source_refs == ()


def test_oldest_pair_eviction_recomputes_closure_without_losing_shared_ref() -> None:
    chunk = candidate("CURRENT")
    old = replace(turn(3), source_refs=(ref(3), ref(1)))
    new = turn(2)
    expected = render_context((chunk,), (new,))
    result = bound_context(
        (chunk,),
        (old, new),
        token_counter=Counter(),
        max_context_tokens=len(expected),
    )
    assert result.model_context == expected
    assert result.source_refs == (ref(1), ref(2))
    assert old.user_input not in result.model_context
    assert old.answer not in result.model_context


def test_pairs_are_removed_before_last_ranked_chunk() -> None:
    first, last = candidate("FIRST"), candidate("LAST" * 100, 2)
    history = turn(3)
    expected = render_context((first,), ())
    result = bound_context(
        (first, last),
        (history,),
        token_counter=Counter(),
        max_context_tokens=len(expected),
    )
    assert result.model_context == expected
    assert result.source_refs == (ref(1),)


def test_bound_helper_applies_fixed_last_four_window() -> None:
    history = tuple(turn(number) for number in range(1, 6))
    result = bound_context((), history, token_counter=Counter())
    assert result.model_context == render_context((), history[-4:])
    assert result.source_refs == tuple(ref(number) for number in range(2, 6))


@pytest.mark.parametrize("budget", [0, 1])
def test_no_partial_data_is_returned_when_nothing_fits(budget: int) -> None:
    result = bound_context(
        (candidate("CURRENT"),),
        (turn(2),),
        token_counter=Counter(),
        max_context_tokens=budget,
    )
    assert result.model_context == ""
    assert result.source_refs == ()


def test_renderer_has_exact_unicode_and_escaping_bytes() -> None:
    rendered = render_context((candidate('Я "x"\n\\'),), ())
    assert rendered == (
        r'{"chunks":[["00000000-0000-0000-0000-000000000001",'
        r'"00000000-0000-0000-0000-00000000000b",'
        r'"00000000-0000-0000-0000-000000000015","Я \"x\"\n\\"]],"version":1}'
    )


def test_empty_input_uses_no_serialization_overhead() -> None:
    assert bound_context((), (), token_counter=Counter()).model_context == ""
