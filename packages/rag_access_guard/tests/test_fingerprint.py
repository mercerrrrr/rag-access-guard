from dataclasses import replace
from uuid import UUID

import pytest
from packages.rag_access_guard.tests.test_history import Counter, Reader, ref
from packages.rag_access_guard.tests.test_history_policy import chunk

from rag_access_guard import Guard, PreparedContext
from rag_access_guard import fingerprint as fingerprint_module
from rag_access_guard.fingerprint import fingerprint


@pytest.mark.anyio
async def test_release_rejects_changed_history_window_configuration() -> None:
    prepared = await Guard(Counter(), max_prior_turns=4).prepare_context(
        UUID(int=1), (chunk(),), (), Reader()
    )
    assert isinstance(prepared, PreparedContext)
    release = await Guard(Counter(), max_prior_turns=0).authorize_release(
        UUID(int=1), UUID(int=2), prepared, Reader()
    )
    assert not release.allowed
    assert release.reason == "invalid_provenance"


@pytest.fixture
def prepared() -> PreparedContext:
    return PreparedContext(
        model_context='Я "x"\n\\',
        source_refs=(ref(1), ref(2)),
        policy_revision=7,
        fingerprint="0" * 64,
    )


def test_fingerprint_has_stable_canonical_bytes(prepared: PreparedContext) -> None:
    assert fingerprint(prepared, "counter", 5000, max_prior_turns=4) == (
        "c7747dbc4e1bf47596a86bf137e0e31dd3ed375a0b3927f33ebf0ecb12394366"
    )


@pytest.mark.parametrize("change", ["text", "refs", "order", "revision"])
def test_fingerprint_binds_every_protected_value(prepared: PreparedContext, change: str) -> None:
    changed = {
        "text": replace(prepared, model_context=prepared.model_context + " "),
        "refs": replace(prepared, source_refs=(ref(1),)),
        "order": replace(prepared, source_refs=(ref(2), ref(1))),
        "revision": replace(prepared, policy_revision=8),
    }[change]
    assert fingerprint(changed, "counter", 5000, max_prior_turns=4) != fingerprint(
        prepared,
        "counter",
        5000,
        max_prior_turns=4,
    )


@pytest.mark.parametrize(
    ("identity", "budget", "prior"),
    [
        ("changed", 5000, 4),
        ("counter", 5001, 4),
        ("counter", 5000, 3),
    ],
)
def test_fingerprint_binds_all_budget_configuration(
    prepared: PreparedContext,
    identity: str,
    budget: int,
    prior: int,
) -> None:
    assert fingerprint(prepared, identity, budget, max_prior_turns=prior) != fingerprint(
        prepared,
        "counter",
        5000,
        max_prior_turns=4,
    )


def test_fingerprint_binds_renderer_revision(
    prepared: PreparedContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = fingerprint(prepared, "counter", 5000, max_prior_turns=4)
    monkeypatch.setattr(fingerprint_module, "RENDERER_REVISION", "changed-renderer")
    assert fingerprint(prepared, "counter", 5000, max_prior_turns=4) != original
