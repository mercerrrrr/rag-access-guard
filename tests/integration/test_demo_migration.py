from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from tests.helpers.demo_seed import owned_fixture_database


def test_v010_head_upgrade_preserves_rows_and_does_not_adopt_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the actual v0.1.0 source head with ordinary populated security/resource tables.
    for target in owned_fixture_database(monkeypatch, migrated=False):
        config = Config("apps/api/alembic.ini")
        command.upgrade(config, "0011_history_closure")
        engine = create_engine(target, hide_parameters=True)
        try:
            user, role, document, grant = (uuid4() for _ in range(4))
            with engine.begin() as connection:
                _ = connection.execute(
                    text(
                        """INSERT INTO users(id,login,display_name,password_hash)
                        VALUES (:id,'legacy','Legacy','synthetic')"""
                    ),
                    {"id": user},
                )
                _ = connection.execute(
                    text("INSERT INTO roles(id,code,display_name) VALUES (:id,'legacy','Legacy')"),
                    {"id": role},
                )
                _ = connection.execute(
                    text("INSERT INTO user_roles(user_id,role_id) VALUES (:user,:role)"),
                    {"user": user, "role": role},
                )
                _ = connection.execute(
                    text("INSERT INTO documents(id,title,created_by) VALUES (:id,'Legacy',:user)"),
                    {"id": document, "user": user},
                )
                _ = connection.execute(
                    text(
                        """INSERT INTO document_grants(id,document_id,user_id,created_by)
                        VALUES (:id,:doc,:user,:user)"""
                    ),
                    {"id": grant, "doc": document, "user": user},
                )
                _ = connection.execute(text("UPDATE policy_state SET revision=7 WHERE id=1"))
                _ = connection.execute(
                    text(
                        """INSERT INTO audit_events
                        (id,event_type,stage,outcome,policy_revision,actor_user_id)
                        VALUES (:id,'user_changed','policy','success',7,:user)"""
                    ),
                    {"id": uuid4(), "user": user},
                )
                queries = {
                    "users": "SELECT row_to_json(t) FROM users t",
                    "roles": "SELECT row_to_json(t) FROM roles t",
                    "user_roles": "SELECT row_to_json(t) FROM user_roles t",
                    "documents": "SELECT row_to_json(t) FROM documents t",
                    "document_grants": "SELECT row_to_json(t) FROM document_grants t",
                    "policy_state": "SELECT row_to_json(t) FROM policy_state t",
                    "audit_events": "SELECT row_to_json(t) FROM audit_events t",
                }
                before = {
                    table: connection.execute(text(query)).scalars().all()
                    for table, query in queries.items()
                }
            # When: the normal locked migration chain advances to the new head.
            command.upgrade(config, "head")
            command.check(config)
            # Then: all prior data survives exactly, and ownership metadata remains empty.
            with engine.connect() as connection:
                after = {
                    table: connection.execute(text(query)).scalars().all()
                    for table, query in queries.items()
                }
                assert after == before
                assert (
                    connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                    == "0014_demo_seed_state"
                )
                assert (
                    connection.execute(text("SELECT count(*) FROM demo_seed_state")).scalar_one()
                    == 0
                )
                assert (
                    connection.execute(text("SELECT count(*) FROM demo_seed_steps")).scalar_one()
                    == 0
                )
        finally:
            engine.dispose()
