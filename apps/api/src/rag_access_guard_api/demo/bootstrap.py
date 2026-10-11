"""Read-only ownership inspection precedes migration; adoption is explicit and serialized."""

from typing import ClassVar, Final

from alembic import command
from alembic.config import Config
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection, create_engine, insert, inspect, select, text
from sqlalchemy.exc import SQLAlchemyError

from rag_access_guard_api.demo import ownership
from rag_access_guard_api.demo.contracts import DemoConfig, DemoMarker
from rag_access_guard_api.demo.errors import DemoOwnershipError
from rag_access_guard_api.demo.files import load_private_json
from rag_access_guard_api.persistence import Base, DemoSeedState, DemoSeedStep
from rag_access_guard_api.services.demo_corpus import REPOSITORY_ROOT

SEED_LOCK: Final = 827_140_014


class SchemaObservation(BaseModel):
    """Validate untrusted catalog values before ownership decisions."""

    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True, frozen=True)
    schema_name: str
    extension_owned: bool


class RelationObservation(SchemaObservation):
    """Bind the relation name and kind to the same catalog observation."""

    table_name: str
    relation_kind: str


def read_marker(connection: Connection) -> DemoMarker | None:
    """Only existing schema is queried; an absent table is not ownership proof."""
    if not inspect(connection).has_table("demo_seed_state"):
        return None
    row = (
        connection.execute(
            select(
                DemoSeedState.instance_id,
                DemoSeedState.dataset_id,
                DemoSeedState.database_name,
                DemoSeedState.database_role,
            )
        )
        .mappings()
        .one_or_none()
    )
    return None if row is None else DemoMarker.model_validate(dict(row))


def verify_database_identity(connection: Connection, marker: DemoMarker) -> None:
    """Observe the actual role, owner, privileges, and preinstalled extension before writes."""
    row = connection.execute(
        text("""SELECT current_database(), current_user, owner.rolname,
        actor.rolsuper, actor.rolcreaterole, actor.rolcreatedb,
        actor.rolreplication, actor.rolbypassrls
        FROM pg_database db JOIN pg_roles owner ON owner.oid=db.datdba
        JOIN pg_roles actor ON actor.rolname=current_user WHERE db.datname=current_database()""")
    ).one()
    if tuple(row[:3]) != (marker.database_name, marker.database_role, marker.database_role) or any(
        row[3:]
    ):
        raise DemoOwnershipError(code="server_identity_mismatch")
    vector = connection.execute(
        text("SELECT extversion FROM pg_extension WHERE extname='vector'")
    ).scalar_one_or_none()
    if vector != "0.8.6":
        raise DemoOwnershipError(code="vector_prerequisite")


def inspect_user_schemas(connection: Connection) -> None:
    """Allow only the known public schema; vector requires no extension-owned schema."""
    schemas = connection.execute(
        text("""SELECT n.nspname AS schema_name, EXISTS(SELECT 1 FROM pg_depend d
        WHERE d.classid='pg_namespace'::regclass AND d.objid=n.oid AND d.deptype='e')
        AS extension_owned
        FROM pg_namespace n WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema'""")
    ).mappings()
    for row in schemas:
        observation = SchemaObservation.model_validate(dict(row))
        if observation.schema_name != "public" or observation.extension_owned:
            raise DemoOwnershipError(code="unowned_schema")


def assert_empty(connection: Connection) -> None:
    """Allow empty application tables; vector catalog types/functions need no relation exemption."""
    inspect_user_schemas(connection)
    relations = connection.execute(
        text("""SELECT n.nspname AS schema_name,c.relname AS table_name,
        c.relkind AS relation_kind,
        EXISTS(SELECT 1 FROM pg_depend d WHERE d.classid='pg_class'::regclass
        AND d.objid=c.oid AND d.deptype='e') AS extension_owned FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT LIKE 'pg_%'
        AND n.nspname<>'information_schema' AND c.relkind IN ('r','p','v','m','f','S')""")
    ).mappings()
    for row in relations:
        observation = RelationObservation.model_validate(dict(row))
        if observation.extension_owned:
            raise DemoOwnershipError(code="unowned_schema")
        if observation.schema_name != "public" or observation.relation_kind not in ("r", "p"):
            raise DemoOwnershipError(code="unowned_schema")
        table = observation.table_name
        if table == "alembic_version":
            continue
        if table not in Base.metadata.tables:
            raise DemoOwnershipError(code="unowned_schema")
        if table == "policy_state":
            rows = connection.execute(text("SELECT id,revision FROM policy_state")).all()
            if rows != [(1, 0)]:
                raise DemoOwnershipError(code="unowned_data")
        elif connection.execute(select(Base.metadata.tables[table]).limit(1)).first() is not None:
            raise DemoOwnershipError(code="unowned_data")


def inspect_target(connection: Connection, config: DemoConfig) -> DemoMarker | None:
    """Require external identity plus either the same marker or a fully empty target."""
    target = ownership.validate_target_locator(config)
    expected = load_private_json(config.ownership_manifest, DemoMarker)
    if (expected.dataset_id, expected.database_name, expected.database_role) != (
        config.dataset_id,
        target.database,
        target.username,
    ):
        raise DemoOwnershipError(code="ownership_manifest_mismatch")
    verify_database_identity(connection, expected)
    current = read_marker(connection)
    if current is None:
        assert_empty(connection)
    elif current != expected:
        raise DemoOwnershipError(code="marker_mismatch")
    return current


def prepare_owned_database(
    config: DemoConfig, *, apply: bool, initialize: bool
) -> DemoMarker | None:
    """Lock only seed coordinators; guarded migrations precede atomic marker/bootstrap creation."""
    _ = ownership.validate_target_locator(config)
    engine = create_engine(
        config.database_url.get_secret_value(),
        hide_parameters=True,
        connect_args={"connect_timeout": 2},
        pool_size=1,
        max_overflow=0,
    )
    try:
        with engine.connect() as connection:
            _ = connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": SEED_LOCK})
            connection.commit()
            current = inspect_target(connection, config)
            connection.commit()
            if not apply:
                return current
            if current is None and not initialize:
                raise DemoOwnershipError(code="initialize_required")
            migration = Config(str(REPOSITORY_ROOT / "apps/api/alembic.ini"))
            migration.set_main_option(
                "demo_database_url", config.database_url.get_secret_value().replace("%", "%%")
            )
            command.upgrade(migration, "head")
            if current is None:
                current = load_private_json(config.ownership_manifest, DemoMarker)
                with connection.begin():
                    _ = connection.execute(
                        text("SELECT id FROM policy_state WHERE id=1 FOR UPDATE")
                    )
                    assert_empty(connection)
                    _ = connection.execute(
                        insert(DemoSeedState).values(id=1, **current.model_dump())
                    )
                    _ = connection.execute(
                        insert(DemoSeedStep).values(
                            instance_id=current.instance_id,
                            key="bootstrap",
                            object_id=current.instance_id,
                        )
                    )
            _ = connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": SEED_LOCK})
            connection.commit()
            return current
    except SQLAlchemyError:
        raise DemoOwnershipError(code="database_unavailable") from None
    finally:
        engine.dispose()
