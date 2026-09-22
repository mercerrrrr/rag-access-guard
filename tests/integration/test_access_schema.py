from sqlalchemy import Connection, inspect, text


def test_access_schema_starts_without_accounts_or_grants(schema_connection: Connection) -> None:
    assert set(inspect(schema_connection).get_table_names()) == {
        "alembic_version",
        "users",
        "sessions",
        "roles",
        "user_roles",
        "documents",
        "document_versions",
        "document_grants",
        "policy_state",
        "audit_events",
        "auth_challenges",
        "auth_rate_buckets",
    }
    assert schema_connection.execute(text("SELECT id, revision FROM policy_state")).one() == (1, 0)
    assert schema_connection.execute(text("SELECT count(*) FROM users")).scalar_one() == 0
    assert schema_connection.execute(text("SELECT count(*) FROM document_grants")).scalar_one() == 0


def test_audit_storage_cannot_accept_content(schema_connection: Connection) -> None:
    columns = {column["name"] for column in inspect(schema_connection).get_columns("audit_events")}
    assert columns == {
        "id",
        "occurred_at",
        "actor_user_id",
        "principal_id",
        "document_id",
        "role_id",
        "grant_id",
        "event_type",
        "stage",
        "outcome",
        "policy_revision",
        "source_count",
    }
