from http import HTTPStatus
from typing import ClassVar, Literal

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from rag_access_guard_api import database
from rag_access_guard_api.main import create_app
from rag_access_guard_api.server import create_event_loop


class ExpectedUnavailableHealth(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    detail: Literal["Service unavailable"]


class ExpectedReadyHealth(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    status: Literal["ok"]


def test_reports_unavailable_before_access_schema_migration(
    isolated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAG_ACCESS_GUARD_DATABASE_URL", isolated_database_url)
    command.upgrade(Config("apps/api/alembic.ini"), "0001_pgvector")
    with TestClient(create_app(), backend_options={"loop_factory": create_event_loop}) as client:
        response = client.get("/api/health/ready")
    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE


def test_reports_unavailable_when_database_cannot_be_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    monkeypatch.setenv(
        "RAG_ACCESS_GUARD_DATABASE_URL",
        "postgresql+psycopg://unavailable@127.0.0.1:1/unavailable",
    )
    application = create_app()

    # When
    with TestClient(
        application,
        backend_options={"loop_factory": create_event_loop},
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/api/health/ready")

    # Then
    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.headers["content-type"] == "application/json"
    assert ExpectedUnavailableHealth.model_validate_json(
        response.content
    ) == ExpectedUnavailableHealth(detail="Service unavailable")


def test_reports_ready_when_database_baseline_is_current() -> None:
    # Given
    application = create_app()

    # When
    with TestClient(
        application,
        backend_options={"loop_factory": create_event_loop},
    ) as client:
        response = client.get("/api/health/ready")

    # Then
    assert response.status_code == HTTPStatus.OK
    assert response.headers["content-type"] == "application/json"
    assert ExpectedReadyHealth.model_validate_json(response.content) == ExpectedReadyHealth(
        status="ok"
    )


@pytest.mark.parametrize(
    ("expected_name", "unexpected_value"),
    [
        ("EXPECTED_ALEMBIC_REVISION", "stale_revision"),
        ("EXPECTED_PGVECTOR_VERSION", "0.0.0"),
        ("EXPECTED_POSTGRESQL_MAJOR", 17),
    ],
    ids=["stale-revision", "wrong-pgvector", "wrong-postgresql"],
)
def test_reports_unavailable_when_database_baseline_differs(
    monkeypatch: pytest.MonkeyPatch,
    expected_name: str,
    unexpected_value: int | str,
) -> None:
    # Given
    monkeypatch.setattr(database, expected_name, unexpected_value)
    application = create_app()

    # When
    with TestClient(
        application,
        backend_options={"loop_factory": create_event_loop},
    ) as client:
        response = client.get("/api/health/ready")

    # Then
    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.headers["content-type"] == "application/json"
    assert ExpectedUnavailableHealth.model_validate_json(
        response.content
    ) == ExpectedUnavailableHealth(detail="Service unavailable")
