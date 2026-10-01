import pytest

from tests.e2e.seed import validate_database_url


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://user:password@example.com:5432/postgres",
        "postgresql+psycopg://user:password@127.0.0.1:5432/rag_access_guard",
        "postgresql+psycopg://user:password@127.0.0.1:5432/postgres?host=example.com",
        "sqlite:///postgres",
    ],
)
def test_launcher_rejects_non_disposable_database_configuration(url: str) -> None:
    with pytest.raises(ValueError, match="explicit loopback PostgreSQL"):
        _ = validate_database_url(url)


def test_launcher_accepts_explicit_local_administration_database() -> None:
    url = "postgresql+psycopg://user:password@127.0.0.1:55467/postgres"
    assert validate_database_url(url).database == "postgres"
