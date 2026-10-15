from typing import Never

import psycopg
import pytest
from tests.helpers.demo_seed import owned_fixture_database


def refuse_connection(conninfo: str, *, autocommit: bool) -> Never:
    _ = conninfo, autocommit
    pytest.fail("An unsafe fixture target reached the connection boundary")


@pytest.mark.parametrize(
    "admin_url",
    [
        "postgresql://synthetic:dummy@remote.invalid:55466/postgres",
        "postgresql://synthetic:dummy@127.0.0.1:55466/postgres?host=remote.invalid",
        "postgresql://synthetic:dummy@127.0.0.1/postgres",
        "postgresql://synthetic:dummy@127.0.0.1:55432/postgres",
        "postgresql://synthetic:dummy@127.0.0.1:invalid/postgres",
    ],
)
def test_unsafe_fixture_admin_is_rejected_before_connecting(
    monkeypatch: pytest.MonkeyPatch, admin_url: str
) -> None:
    # Given: explicit untrusted test configuration and a forbidden connection boundary.
    monkeypatch.setenv("RAG_E2E_DATABASE_ADMIN_URL", admin_url)
    monkeypatch.setattr(psycopg, "connect", refuse_connection)
    # When/Then: target refusal occurs before any connection, including cleanup.
    with pytest.raises((AssertionError, ValueError)):
        _ = next(owned_fixture_database(monkeypatch))


def test_missing_fixture_admin_never_falls_back_to_application_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an application target cannot authorize test administration implicitly.
    monkeypatch.delenv("RAG_E2E_DATABASE_ADMIN_URL", raising=False)
    monkeypatch.setenv(
        "RAG_ACCESS_GUARD_DATABASE_URL",
        "postgresql://synthetic:dummy@127.0.0.1:55466/user_database",
    )
    monkeypatch.setattr(psycopg, "connect", refuse_connection)
    # When/Then: missing explicit admin configuration fails before connection.
    with pytest.raises(KeyError, match="RAG_E2E_DATABASE_ADMIN_URL"):
        _ = next(owned_fixture_database(monkeypatch))
