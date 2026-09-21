"""Create identity, document access, revision and audit storage."""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_access_control"
down_revision: str | None = "0001_pgvector"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Install the access schema without accounts or implicit grants."""
    op.execute("""
        CREATE TABLE users (
            id uuid CONSTRAINT pk_users PRIMARY KEY,
            login varchar(254) NOT NULL CONSTRAINT uq_users_login UNIQUE,
            display_name varchar(200) NOT NULL,
            password_hash text NOT NULL,
            is_active boolean NOT NULL DEFAULT true,
            is_admin boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_users_login_normalized
                CHECK (login = lower(btrim(login)) AND length(login) > 0),
            CONSTRAINT ck_users_display_name_nonblank CHECK (length(btrim(display_name)) > 0),
            CONSTRAINT ck_users_password_hash_nonblank CHECK (length(btrim(password_hash)) > 0)
        );
        CREATE TABLE sessions (
            id uuid CONSTRAINT pk_sessions PRIMARY KEY,
            user_id uuid NOT NULL CONSTRAINT fk_sessions_user_id_users
                REFERENCES users(id) ON DELETE RESTRICT,
            token_digest bytea NOT NULL CONSTRAINT uq_sessions_token_digest UNIQUE,
            csrf_token_digest bytea NOT NULL CONSTRAINT uq_sessions_csrf_token_digest UNIQUE,
            created_at timestamptz NOT NULL DEFAULT now(),
            last_seen_at timestamptz NOT NULL DEFAULT now(),
            absolute_expires_at timestamptz NOT NULL,
            revoked_at timestamptz,
            CONSTRAINT ck_sessions_token_digest_length CHECK (octet_length(token_digest) = 32),
            CONSTRAINT ck_sessions_csrf_token_digest_length
                CHECK (octet_length(csrf_token_digest) = 32),
            CONSTRAINT ck_sessions_last_seen_order CHECK (last_seen_at >= created_at),
            CONSTRAINT ck_sessions_expiry_order CHECK (last_seen_at < absolute_expires_at),
            CONSTRAINT ck_sessions_revoked_order CHECK (revoked_at >= created_at)
        );
        CREATE INDEX ix_sessions_user_id ON sessions (user_id);
        CREATE INDEX ix_sessions_absolute_expires_at ON sessions (absolute_expires_at);
        CREATE TABLE roles (
            id uuid CONSTRAINT pk_roles PRIMARY KEY,
            code varchar(64) NOT NULL CONSTRAINT uq_roles_code UNIQUE,
            display_name varchar(200) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_roles_code_format CHECK (code ~ '^[a-z][a-z0-9_]{0,63}$'),
            CONSTRAINT ck_roles_display_name_nonblank CHECK (length(btrim(display_name)) > 0)
        );
        CREATE TABLE user_roles (
            user_id uuid NOT NULL CONSTRAINT fk_user_roles_user_id_users
                REFERENCES users(id) ON DELETE RESTRICT,
            role_id uuid NOT NULL CONSTRAINT fk_user_roles_role_id_roles
                REFERENCES roles(id) ON DELETE RESTRICT,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_user_roles PRIMARY KEY (user_id, role_id)
        );
        CREATE INDEX ix_user_roles_role_id ON user_roles (role_id);
        CREATE TABLE documents (
            id uuid CONSTRAINT pk_documents PRIMARY KEY,
            title varchar(512) NOT NULL,
            created_by uuid NOT NULL CONSTRAINT fk_documents_created_by_users
                REFERENCES users(id) ON DELETE RESTRICT,
            created_at timestamptz NOT NULL DEFAULT now(),
            is_active boolean NOT NULL DEFAULT true,
            CONSTRAINT ck_documents_title_nonblank CHECK (length(btrim(title)) > 0)
        );
        CREATE INDEX ix_documents_created_by ON documents (created_by);
        CREATE TABLE document_grants (
            id uuid CONSTRAINT pk_document_grants PRIMARY KEY,
            document_id uuid NOT NULL CONSTRAINT fk_document_grants_document_id_documents
                REFERENCES documents(id) ON DELETE RESTRICT,
            user_id uuid CONSTRAINT fk_document_grants_user_id_users
                REFERENCES users(id) ON DELETE RESTRICT,
            role_id uuid CONSTRAINT fk_document_grants_role_id_roles
                REFERENCES roles(id) ON DELETE RESTRICT,
            created_by uuid NOT NULL CONSTRAINT fk_document_grants_created_by_users
                REFERENCES users(id) ON DELETE RESTRICT,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_document_grants_exactly_one_subject
                CHECK ((user_id IS NULL) <> (role_id IS NULL))
        );
        CREATE UNIQUE INDEX uq_document_grants_document_user
            ON document_grants (document_id, user_id) WHERE user_id IS NOT NULL;
        CREATE UNIQUE INDEX uq_document_grants_document_role
            ON document_grants (document_id, role_id) WHERE role_id IS NOT NULL;
        CREATE INDEX ix_document_grants_user_id ON document_grants (user_id);
        CREATE INDEX ix_document_grants_role_id ON document_grants (role_id);
        CREATE INDEX ix_document_grants_created_by ON document_grants (created_by);
        CREATE TABLE policy_state (
            id smallint CONSTRAINT pk_policy_state PRIMARY KEY,
            revision bigint NOT NULL DEFAULT 0,
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_policy_state_singleton CHECK (id = 1),
            CONSTRAINT ck_policy_state_revision_nonnegative CHECK (revision >= 0)
        );
        INSERT INTO policy_state (id, revision) VALUES (1, 0);
        CREATE TABLE audit_events (
            id uuid CONSTRAINT pk_audit_events PRIMARY KEY,
            occurred_at timestamptz NOT NULL DEFAULT now(),
            actor_user_id uuid CONSTRAINT fk_audit_events_actor_user_id_users
                REFERENCES users(id) ON DELETE RESTRICT,
            principal_id uuid,
            document_id uuid,
            role_id uuid,
            grant_id uuid,
            event_type varchar(32) NOT NULL,
            stage varchar(16) NOT NULL,
            outcome varchar(16) NOT NULL,
            policy_revision bigint NOT NULL,
            source_count integer NOT NULL DEFAULT 0,
            CONSTRAINT ck_audit_events_event_type CHECK (event_type IN (
                'session_created', 'session_revoked', 'login_denied',
                'user_changed', 'role_changed', 'membership_added', 'membership_removed',
                'document_changed', 'grant_added', 'grant_removed', 'access_checked'
            )),
            CONSTRAINT ck_audit_events_stage CHECK (
                stage IN ('authentication', 'policy', 'retrieval', 'context', 'release', 'read')
            ),
            CONSTRAINT ck_audit_events_outcome
                CHECK (outcome IN ('allowed', 'denied', 'success', 'failure')),
            CONSTRAINT ck_audit_events_policy_revision_nonnegative CHECK (policy_revision >= 0),
            CONSTRAINT ck_audit_events_source_count_nonnegative CHECK (source_count >= 0)
        );
        CREATE INDEX ix_audit_events_occurred_at ON audit_events (occurred_at);
        CREATE INDEX ix_audit_events_actor_user_id ON audit_events (actor_user_id);
    """)


def downgrade() -> None:
    """Remove access data while retaining the pgvector baseline."""
    op.execute("""
        DROP TABLE audit_events;
        DROP TABLE document_grants;
        DROP TABLE user_roles;
        DROP TABLE sessions;
        DROP TABLE documents;
        DROP TABLE roles;
        DROP TABLE users;
        DROP TABLE policy_state;
    """)
