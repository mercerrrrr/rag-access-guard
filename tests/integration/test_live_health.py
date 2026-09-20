from http import HTTPStatus
from typing import ClassVar, Literal

from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from rag_access_guard_api.main import app


class ExpectedLiveHealth(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    status: Literal["ok"]


def test_reports_ok_when_api_process_is_live() -> None:
    # Given
    client = TestClient(app)

    # When
    response = client.get("/api/health/live")

    # Then
    assert response.status_code == HTTPStatus.OK
    assert response.headers["content-type"] == "application/json"
    assert ExpectedLiveHealth.model_validate_json(response.content) == ExpectedLiveHealth(
        status="ok"
    )
