import pytest
from pydantic import ValidationError

from rag_access_guard_api.config import Settings


@pytest.mark.parametrize("lease", [119, 120])
def test_startup_rejects_lease_not_exceeding_both_attempts(
    monkeypatch: pytest.MonkeyPatch, lease: int
) -> None:
    monkeypatch.setenv("RAG_ACCESS_GUARD_GENERATION_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("RAG_ACCESS_GUARD_PENDING_LEASE_SECONDS", str(lease))
    with pytest.raises(ValidationError, match="Lease must exceed both generation attempts"):
        _ = Settings()


@pytest.mark.parametrize("timeout", ["0", "-1", "61"])
def test_startup_rejects_unbounded_attempt_timeout(
    monkeypatch: pytest.MonkeyPatch, timeout: str
) -> None:
    monkeypatch.setenv("RAG_ACCESS_GUARD_GENERATION_TIMEOUT_SECONDS", timeout)
    with pytest.raises(ValidationError):
        _ = Settings()


def test_default_budget_and_shorter_operator_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings()
    assert settings.generation_timeout_seconds == 60
    assert settings.pending_lease_seconds == 180
    monkeypatch.setenv("RAG_ACCESS_GUARD_GENERATION_TIMEOUT_SECONDS", "10")
    monkeypatch.setenv("RAG_ACCESS_GUARD_PENDING_LEASE_SECONDS", "21")
    shortened = Settings()
    assert shortened.generation_timeout_seconds == 10
    assert shortened.pending_lease_seconds == 21
