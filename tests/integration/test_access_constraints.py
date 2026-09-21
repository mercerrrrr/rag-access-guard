import psycopg
import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError


@pytest.mark.parametrize(
    ("statement", "constraint"),
    [
        ("UPDATE users SET login = ' Reader '", "ck_users_login_normalized"),
        ("UPDATE users SET login = ''", "ck_users_login_normalized"),
        ("UPDATE users SET display_name = ' '", "ck_users_display_name_nonblank"),
        ("UPDATE users SET password_hash = ''", "ck_users_password_hash_nonblank"),
        ("UPDATE roles SET code = 'Engineering'", "ck_roles_code_format"),
        ("UPDATE roles SET display_name = ' '", "ck_roles_display_name_nonblank"),
        ("UPDATE documents SET title = ' '", "ck_documents_title_nonblank"),
        ("UPDATE document_grants SET user_id = NULL", "ck_document_grants_exactly_one_subject"),
        (
            "UPDATE document_grants SET user_id = (SELECT id FROM users)",
            "ck_document_grants_exactly_one_subject",
        ),
        (
            "UPDATE sessions SET token_digest = decode('aa', 'hex')",
            "ck_sessions_token_digest_length",
        ),
        (
            "UPDATE sessions SET csrf_token_digest = decode('aa', 'hex')",
            "ck_sessions_csrf_token_digest_length",
        ),
        (
            "UPDATE sessions SET last_seen_at = created_at - interval '1 second'",
            "ck_sessions_last_seen_order",
        ),
        ("UPDATE sessions SET absolute_expires_at = last_seen_at", "ck_sessions_expiry_order"),
        (
            "UPDATE sessions SET revoked_at = created_at - interval '1 second'",
            "ck_sessions_revoked_order",
        ),
        ("INSERT INTO policy_state (id, revision) VALUES (2, 0)", "ck_policy_state_singleton"),
        ("UPDATE policy_state SET revision = -1", "ck_policy_state_revision_nonnegative"),
        ("UPDATE audit_events SET event_type = 'arbitrary text'", "ck_audit_events_event_type"),
        ("UPDATE audit_events SET stage = 'prompt'", "ck_audit_events_stage"),
        ("UPDATE audit_events SET outcome = 'document text'", "ck_audit_events_outcome"),
        (
            "UPDATE audit_events SET policy_revision = -1",
            "ck_audit_events_policy_revision_nonnegative",
        ),
        ("UPDATE audit_events SET source_count = -1", "ck_audit_events_source_count_nonnegative"),
        ("UPDATE sessions SET user_id = gen_random_uuid()", "fk_sessions_user_id_users"),
        ("UPDATE user_roles SET user_id = gen_random_uuid()", "fk_user_roles_user_id_users"),
        ("UPDATE user_roles SET role_id = gen_random_uuid()", "fk_user_roles_role_id_roles"),
        ("UPDATE documents SET created_by = gen_random_uuid()", "fk_documents_created_by_users"),
        (
            "UPDATE document_grants SET document_id = gen_random_uuid()",
            "fk_document_grants_document_id_documents",
        ),
        (
            "UPDATE document_grants SET user_id = gen_random_uuid() WHERE user_id IS NOT NULL",
            "fk_document_grants_user_id_users",
        ),
        (
            "UPDATE document_grants SET role_id = gen_random_uuid() WHERE role_id IS NOT NULL",
            "fk_document_grants_role_id_roles",
        ),
        (
            "UPDATE document_grants SET created_by = gen_random_uuid()",
            "fk_document_grants_created_by_users",
        ),
        (
            "UPDATE audit_events SET actor_user_id = gen_random_uuid()",
            "fk_audit_events_actor_user_id_users",
        ),
        ("INSERT INTO policy_state (id, revision) VALUES (1, 0)", "pk_policy_state"),
        (
            """INSERT INTO users (id, login, display_name, password_hash)
               SELECT gen_random_uuid(), login, display_name, password_hash FROM users""",
            "uq_users_login",
        ),
        (
            """INSERT INTO roles (id, code, display_name)
               SELECT gen_random_uuid(), code, display_name FROM roles""",
            "uq_roles_code",
        ),
        ("INSERT INTO user_roles SELECT * FROM user_roles", "pk_user_roles"),
        (
            """INSERT INTO document_grants (id, document_id, user_id, created_by)
               SELECT gen_random_uuid(), document_id, user_id, created_by
               FROM document_grants WHERE user_id IS NOT NULL""",
            "uq_document_grants_document_user",
        ),
        (
            """INSERT INTO document_grants (id, document_id, role_id, created_by)
               SELECT gen_random_uuid(), document_id, role_id, created_by
               FROM document_grants WHERE role_id IS NOT NULL""",
            "uq_document_grants_document_role",
        ),
        (
            """INSERT INTO sessions
               (id, user_id, token_digest, csrf_token_digest, absolute_expires_at)
               SELECT gen_random_uuid(), user_id, token_digest, decode(repeat('ef',32),'hex'),
                      absolute_expires_at FROM sessions""",
            "uq_sessions_token_digest",
        ),
        (
            """INSERT INTO sessions
               (id, user_id, token_digest, csrf_token_digest, absolute_expires_at)
               SELECT gen_random_uuid(), user_id, decode(repeat('ef',32),'hex'), csrf_token_digest,
                      absolute_expires_at FROM sessions""",
            "uq_sessions_csrf_token_digest",
        ),
        ("DELETE FROM documents", "fk_document_grants_document_id_documents"),
    ],
)
def test_invalid_records_are_rejected(
    seeded_connection: Connection, statement: str, constraint: str
) -> None:
    with pytest.raises(IntegrityError) as failure, seeded_connection.begin_nested():
        _ = seeded_connection.execute(text(statement))
    assert isinstance(failure.value.orig, psycopg.Error)
    assert failure.value.orig.diag.constraint_name == constraint


@pytest.mark.parametrize("statement", ["DELETE FROM users", "DELETE FROM roles"])
def test_parent_deletion_never_cascades(seeded_connection: Connection, statement: str) -> None:
    with pytest.raises(IntegrityError) as failure, seeded_connection.begin_nested():
        _ = seeded_connection.execute(text(statement))
    assert isinstance(failure.value.orig, psycopg.Error)
    assert failure.value.orig.sqlstate == "23001"
    assert seeded_connection.execute(text("SELECT count(*) FROM document_grants")).scalar_one() == 2


def test_direct_grant_survives_removal_of_role_and_membership(
    seeded_connection: Connection,
) -> None:
    _ = seeded_connection.execute(text("DELETE FROM document_grants WHERE role_id IS NOT NULL"))
    _ = seeded_connection.execute(text("DELETE FROM user_roles"))
    _ = seeded_connection.execute(text("DELETE FROM roles"))
    assert (
        seeded_connection.execute(
            text("SELECT count(*) FROM document_grants WHERE user_id IS NOT NULL")
        ).scalar_one()
        == 1
    )


def test_audit_keeps_resource_identifiers_after_grant_deletion(
    seeded_connection: Connection,
) -> None:
    _ = seeded_connection.execute(
        text(
            """UPDATE audit_events SET grant_id =
               (SELECT id FROM document_grants WHERE user_id IS NOT NULL)"""
        )
    )
    _ = seeded_connection.execute(text("DELETE FROM document_grants WHERE user_id IS NOT NULL"))
    assert (
        seeded_connection.execute(
            text("SELECT grant_id IS NOT NULL FROM audit_events")
        ).scalar_one()
        is True
    )
