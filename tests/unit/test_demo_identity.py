from dataclasses import replace

import pytest

from rag_access_guard_api.services.evaluation_identity import profile_hash
from rag_access_guard_api.services.model_profiles import DEMO_PROFILE, ModelProfile


def test_identity_binds_actual_rendered_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    original = profile_hash(DEMO_PROFILE)
    render = ModelProfile.render

    def changed(self: ModelProfile, *, user_input: str, system_supplied_context: str) -> str:
        return (
            render(self, user_input=user_input, system_supplied_context=system_supplied_context)
            + "+"
        )

    monkeypatch.setattr(ModelProfile, "render", changed)
    assert profile_hash(DEMO_PROFILE) != original


def test_identity_binds_selected_manifest() -> None:
    original = profile_hash(DEMO_PROFILE)
    changed = replace(DEMO_PROFILE, tokenizer_sha256="a" * 64)
    assert profile_hash(changed) != original
