from pathlib import Path

import pytest
from experiments.comparison import ComparisonConfig
from experiments.database import ExperimentDatabases
from experiments.harness import ScenarioHarness
from sqlalchemy.engine import make_url
from tests.integration.search_fixtures import configure_search

from rag_access_guard_api.config import Settings


@pytest.fixture
def comparison_config() -> ComparisonConfig:
    return ComparisonConfig()


@pytest.fixture
def comparison_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ScenarioHarness:
    configure_search(tmp_path / "retrieval.json", monkeypatch)
    url = make_url(str(Settings().database_url)).set(database="postgres")
    return ScenarioHarness(ExperimentDatabases(url.render_as_string(hide_password=False)))
