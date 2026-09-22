import pytest
from pydantic import ValidationError

from rag_access_guard_api.config import Settings


@pytest.mark.parametrize(
    "origin", ["http://remote.test", "http://192.0.2.1", "https://localhost:5173"]
)
def test_loopback_mode_rejects_remote_origin(monkeypatch: pytest.MonkeyPatch, origin: str) -> None:
    monkeypatch.setenv("RAG_ACCESS_GUARD_LOOPBACK_DEVELOPMENT", "true")
    monkeypatch.setenv("RAG_ACCESS_GUARD_AUTH_ORIGIN", origin)
    with pytest.raises(ValidationError):
        _ = Settings()


@pytest.mark.parametrize(
    "origin",
    [
        "null",
        "https://*.test",
        "https://rag.test/path",
        "https://user@rag.test",
        "http://localhost:5173",
    ],
)
def test_https_mode_requires_exact_secure_origin(
    monkeypatch: pytest.MonkeyPatch, origin: str
) -> None:
    monkeypatch.setenv("RAG_ACCESS_GUARD_AUTH_ORIGIN", origin)
    with pytest.raises(ValidationError):
        _ = Settings()


def test_invalid_limit_secret_is_not_echoed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_ACCESS_GUARD_AUTH_LIMIT_SECRET", "synthetic-invalid-value")
    with pytest.raises(ValidationError) as error:
        _ = Settings()
    assert "synthetic-invalid-value" not in str(error.value)


def test_missing_limit_secret_is_configuration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RAG_ACCESS_GUARD_AUTH_LIMIT_SECRET")
    with pytest.raises(ValidationError):
        _ = Settings()
