from collections.abc import Mapping
from pathlib import Path
from typing import Never
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy.engine import make_url
from tests.helpers.demo_bootstrap import RecordingWrites

from rag_access_guard_api.demo import bootstrap
from rag_access_guard_api.demo.contracts import DemoConfig, DemoMarker, DemoRuntime
from rag_access_guard_api.demo.ownership import DemoOwnershipError, validate_owned_target


def config_for(
    tmp_path: Path,
    url: str = "postgresql+psycopg://demo_role:PRIVATE@127.0.0.1/rag_access_guard_demo",
) -> DemoConfig:
    return DemoConfig(
        dataset_id="fixture-v1",
        database_url=SecretStr(url),
        corpus_manifest=tmp_path / "manifest.json",
        ownership_manifest=tmp_path / "owner.json",
        credentials_path=tmp_path / "credentials.json",
        access_definition=tmp_path / "access.json",
        model_profile="qwen3-instruct-demo-v1",
        retrieval_config=tmp_path / "retrieval.json",
        runtime=DemoRuntime(
            repository_root=tmp_path,
            state_directory=tmp_path,
            log_directory=tmp_path,
            ollama_supervisor_config=tmp_path / "ollama.json",
        ),
    )


def marker_for() -> DemoMarker:
    return DemoMarker(
        instance_id=UUID(int=1),
        dataset_id="fixture-v1",
        database_name="rag_access_guard_demo",
        database_role="demo_role",
    )


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://demo_role:PRIVATE@remote.example/rag_access_guard_demo",
        "postgresql+psycopg://demo_role:PRIVATE@127.0.0.1/rag_access_guard_demo_old",
        "postgresql+psycopg://postgres:PRIVATE@127.0.0.1/rag_access_guard_demo",
    ],
)
def test_target_denied_before_writes_when_identity_is_unsafe(
    tmp_path: Path, url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an untrusted target URL and an apparently valid ownership marker.
    config = config_for(tmp_path, url)
    engine_calls: list[str] = []
    migrations: list[str] = []

    def denied_engine(
        _url: str,
        *,
        hide_parameters: bool,
        connect_args: Mapping[str, int],
        pool_size: int,
        max_overflow: int,
    ) -> Never:
        engine_calls.append("constructed")
        assert hide_parameters
        assert connect_args == {"connect_timeout": 2}
        assert pool_size == 1
        assert max_overflow == 0
        message = "unexpected database connection"
        raise AssertionError(message)

    def recorded_upgrade(_migration: Config, revision: str) -> None:
        migrations.append(revision)

    monkeypatch.setattr(bootstrap, "create_engine", denied_engine)
    monkeypatch.setattr(command, "upgrade", recorded_upgrade)
    # When: the target boundary evaluates ownership.
    with (
        RecordingWrites(make_url(url)) as recording,
        pytest.raises(DemoOwnershipError, match="unsafe_target") as failure,
    ):
        _ = bootstrap.prepare_owned_database(config, apply=True, initialize=True)
    # Then: the safe rejection exposes neither URI nor its credential.
    assert "PRIVATE" not in str(failure.value)
    assert "://" not in str(failure.value)
    assert recording.write_count == 0
    assert engine_calls == []
    assert migrations == []


def test_target_denied_when_marker_is_absent(tmp_path: Path) -> None:
    # Given: a correctly named local target without ownership proof.
    config = config_for(tmp_path)
    # When/Then: plain apply cannot adopt it.
    with pytest.raises(DemoOwnershipError):
        validate_owned_target(config, None)


def test_marker_instance_mismatch_is_denied(tmp_path: Path) -> None:
    # Given: the private manifest names another local instance.
    config = config_for(tmp_path)
    _ = config.ownership_manifest.write_text(marker_for().model_dump_json(), encoding="utf-8")
    mismatched = marker_for().model_copy(update={"instance_id": UUID(int=2)})
    # When/Then: an otherwise identical database marker fails closed.
    with pytest.raises(DemoOwnershipError, match="marker_mismatch"):
        validate_owned_target(config, mismatched)


def test_malformed_port_is_sanitized_before_engine_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a malformed private locator and a recording no-connection boundary.
    config = config_for(
        tmp_path,
        "postgresql+psycopg://demo_role:PRIVATE@127.0.0.1:not-a-port/rag_access_guard_demo",
    )
    created = False

    def create_engine(*_args: object, **_kwargs: object) -> Never:
        nonlocal created
        created = True
        message = "unexpected database connection"
        raise AssertionError(message)

    monkeypatch.setattr(bootstrap, "create_engine", create_engine)
    # When/Then: no engine is constructed and only a stable safe error is raised.
    with pytest.raises(DemoOwnershipError, match="invalid_database_url") as error:
        _ = bootstrap.prepare_owned_database(config, apply=True, initialize=True)
    assert not created
    assert "PRIVATE" not in str(error.value)
    assert "not-a-port" not in str(error.value)
