import sys

import pytest
from pydantic import SecretStr, ValidationError

from rag_access_guard_api.adapters import embeddings
from rag_access_guard_api.adapters.embeddings import (
    E5EmbeddingAdapter,
    EmbeddingError,
    IsolatedE5EmbeddingAdapter,
)
from rag_access_guard_api.config import Settings


@pytest.fixture
def cpu_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_ACCESS_GUARD_MODEL_PROFILE", "qwen35-extractive-cpu-demo-v1")
    monkeypatch.delenv("RAG_ACCESS_GUARD_E5_EXECUTION", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")


@pytest.mark.usefixtures("cpu_profile")
def test_default_cpu_factory_keeps_physical_isolation() -> None:
    adapter = embeddings.get_embedding_adapter()
    assert isinstance(adapter, IsolatedE5EmbeddingAdapter)
    assert adapter.retain_model is False


@pytest.mark.usefixtures("cpu_profile")
def test_explicit_resident_factory_reuses_existing_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_ACCESS_GUARD_E5_EXECUTION", "resident")
    adapter = embeddings.get_embedding_adapter()
    assert type(adapter) is E5EmbeddingAdapter
    assert adapter.retain_model is True
    assert adapter.cache_queries is True


@pytest.mark.usefixtures("cpu_profile")
@pytest.mark.parametrize("mode", ["unknown", "Resident", "", "auto"])
def test_unknown_execution_mode_denies_before_runtime(
    mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAG_ACCESS_GUARD_E5_EXECUTION", mode)

    def forbidden_runtime() -> None:
        pytest.fail("invalid mode must not create a runtime")

    monkeypatch.setattr(embeddings, "current_runtime", forbidden_runtime)
    with pytest.raises(EmbeddingError):
        _ = embeddings.get_embedding_adapter()


@pytest.mark.usefixtures("cpu_profile")
def test_residency_does_not_silently_change_another_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_ACCESS_GUARD_E5_EXECUTION", "resident")
    monkeypatch.setenv("RAG_ACCESS_GUARD_MODEL_PROFILE", "qwen3-thinking-legacy-v1")
    with pytest.raises(EmbeddingError):
        _ = embeddings.get_embedding_adapter()


def settings(execution: str, *, profile: str = "qwen35-extractive-cpu-demo-v1") -> Settings:
    return Settings.model_validate(
        {
            "auth_limit_secret": "1" * 64,
            "auth_origin": "http://127.0.0.1:4173",
            "loopback_development": True,
            "llm_adapter": "ollama",
            "model_profile": profile,
            "e5_execution": execution,
        }
    )


def test_settings_expose_explicit_resident_opt_in() -> None:
    assert settings("resident").e5_execution == "resident"
    assert settings("isolated").e5_execution == "isolated"


def test_settings_reject_unknown_residency() -> None:
    with pytest.raises(ValidationError):
        _ = settings("unknown")


def test_settings_reject_residency_for_other_profiles() -> None:
    with pytest.raises(ValidationError):
        _ = settings("resident", profile="qwen3-thinking-legacy-v1")


@pytest.mark.parametrize("local", [True, False])
def test_resident_settings_require_real_local_demo(*, local: bool) -> None:
    with pytest.raises(ValidationError):
        _ = Settings(
            auth_limit_secret=SecretStr("1" * 64),
            auth_origin="http://127.0.0.1:4173" if local else "https://rag.test",
            loopback_development=local,
            llm_adapter="disabled" if local else "ollama",
            model_profile="qwen35-extractive-cpu-demo-v1",
            e5_execution="resident",
        )
