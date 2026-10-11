import os
from pathlib import Path
from typing import Literal, assert_never
from uuid import UUID, uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from rag_access_guard_api.demo import bootstrap
from rag_access_guard_api.demo.contracts import DemoMarker
from rag_access_guard_api.demo.errors import DemoOwnershipError
from tests.helpers.demo_bootstrap import RecordingWrites
from tests.helpers.demo_seed import owned_fixture_database, seed_config


def test_populated_extension_owned_table_is_denied_without_writes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: user storage attached to vector only in this owned UUID fixture database.
    migrations: list[str] = []
    upgrade = command.upgrade

    def recorded_upgrade(migration: Config, revision: str) -> None:
        migrations.append(revision)
        upgrade(migration, revision)

    for target in owned_fixture_database(monkeypatch, migrated=False):
        config = seed_config(tmp_path, target)
        engine = create_engine(target, hide_parameters=True)
        try:
            with engine.begin() as connection:
                _ = connection.execute(text("CREATE TABLE public.extension_payload(value integer)"))
                _ = connection.execute(text("INSERT INTO public.extension_payload VALUES (7)"))
            admin = make_url(os.environ["RAG_E2E_DATABASE_ADMIN_URL"])
            assert admin.host in {"localhost", "127.0.0.1", "::1"}
            assert admin.port == 55466
            assert not admin.query
            assert target.database is not None
            assert target.database.startswith("rag_access_guard_test_")
            _ = UUID(hex=target.database.removeprefix("rag_access_guard_test_"))
            with psycopg.connect(
                admin.set(drivername="postgresql", database=target.database).render_as_string(
                    hide_password=False
                ),
                autocommit=True,
            ) as connection:
                _ = connection.execute("ALTER EXTENSION vector ADD TABLE public.extension_payload")
            monkeypatch.setattr(command, "upgrade", recorded_upgrade)
            # When: the actual pre-migration boundary considers initialization.
            failure: DemoOwnershipError | None = None
            with RecordingWrites(target) as recording:
                try:
                    _ = bootstrap.prepare_owned_database(config, apply=True, initialize=True)
                except DemoOwnershipError as error:
                    failure = error
            # Then: no migration, application write or data change occurred.
            assert migrations == []
            assert recording.write_count == 0
            assert failure is not None
            assert failure.code == "unowned_schema"
            with engine.connect() as connection:
                assert (
                    connection.execute(
                        text("SELECT value FROM public.extension_payload")
                    ).scalar_one()
                    == 7
                )
                assert (
                    connection.execute(
                        text("SELECT to_regclass('public.alembic_version')")
                    ).scalar_one()
                    is None
                )
                assert (
                    connection.execute(
                        text("SELECT to_regclass('public.demo_seed_state')")
                    ).scalar_one()
                    is None
                )
        finally:
            engine.dispose()


@pytest.mark.parametrize("state", ["absent", "mismatched", "nonempty"])
def test_prepare_refusals_have_zero_writes_before_migration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    state: Literal["absent", "mismatched", "nonempty"],
) -> None:
    # Given: real low-privilege ownership observations with an ineligible marker/data state.
    migrations: list[str] = []

    def recorded_upgrade(_migration: Config, revision: str) -> None:
        migrations.append(revision)

    match state:
        case "absent":
            migrated, expected = False, "initialize_required"
        case "mismatched":
            migrated, expected = True, "marker_mismatch"
        case "nonempty":
            migrated, expected = True, "unowned_data"
        case _:
            assert_never(state)
    for target in owned_fixture_database(monkeypatch, migrated=migrated):
        config = seed_config(tmp_path, target)
        engine = create_engine(target, hide_parameters=True)
        try:
            match state:
                case "mismatched":
                    marker = DemoMarker.model_validate_json(config.ownership_manifest.read_bytes())
                    wrong = marker.model_copy(update={"instance_id": UUID(int=2)})
                    with engine.begin() as connection:
                        _ = connection.execute(
                            text("""INSERT INTO demo_seed_state
                            (id,instance_id,dataset_id,database_name,database_role)
                            VALUES (1,:instance_id,:dataset_id,:database_name,:database_role)"""),
                            wrong.model_dump(),
                        )
                case "nonempty":
                    with engine.begin() as connection:
                        _ = connection.execute(
                            text("""INSERT INTO users(id,login,display_name,password_hash)
                            VALUES (:id,'preexisting','Existing','synthetic')"""),
                            {"id": uuid4()},
                        )
                case "absent":
                    pass
                case _:
                    assert_never(state)

            monkeypatch.setattr(command, "upgrade", recorded_upgrade)
            # When: plain apply crosses the real pre-migration inspection boundary.
            with (
                RecordingWrites(target) as recording,
                pytest.raises(DemoOwnershipError) as failure,
            ):
                _ = bootstrap.prepare_owned_database(config, apply=True, initialize=False)
            # Then: refusal cannot invoke migration or write into the target.
            assert recording.write_count == 0
            assert migrations == []
            assert failure.value.code == expected
        finally:
            engine.dispose()
