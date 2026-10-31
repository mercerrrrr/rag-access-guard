from pathlib import Path
from typing import Literal

import pytest
from pydantic import SecretStr, ValidationError

from rag_access_guard_api.config import Settings
from rag_access_guard_api.services.model_profiles import ProfileId


@pytest.mark.parametrize(
    ("adapter", "profile", "local"),
    [
        ("fake", "qwen25-extractive-small-demo-v3", True),
        ("ollama", "qwen25-extractive-small-demo-v2", True),
        ("ollama", "qwen25-extractive-small-demo-v3", False),
    ],
)
def test_relevance_requires_the_explicit_local_real_model_bundle(
    tmp_path: Path, adapter: Literal["fake", "ollama"], profile: ProfileId, *, local: bool
) -> None:
    # Given a requested relevance configuration outside its supported runtime contract.
    # When parsing operator settings, then reject rather than silently ignore the feature.
    with pytest.raises(ValidationError, match="Relevance triage"):
        _ = Settings(
            auth_limit_secret=SecretStr("11" * 32),
            auth_origin="http://127.0.0.1:4173" if local else "https://localhost:5173",
            loopback_development=local,
            llm_adapter=adapter,
            model_profile=profile,
            relevance_config_path=tmp_path / "relevance.json",
        )


def test_local_real_bundle_requires_its_relevance_config() -> None:
    with pytest.raises(ValidationError, match="Relevance triage"):
        _ = Settings(
            auth_limit_secret=SecretStr("11" * 32),
            auth_origin="http://127.0.0.1:4173",
            loopback_development=True,
            llm_adapter="ollama",
            model_profile="qwen25-extractive-small-demo-v3",
        )


def test_local_real_bundle_accepts_its_explicit_config(tmp_path: Path) -> None:
    settings = Settings(
        auth_limit_secret=SecretStr("11" * 32),
        auth_origin="http://127.0.0.1:4173",
        loopback_development=True,
        llm_adapter="ollama",
        model_profile="qwen25-extractive-small-demo-v3",
        relevance_config_path=tmp_path / "relevance.json",
    )
    assert settings.relevance_config_path == tmp_path / "relevance.json"
