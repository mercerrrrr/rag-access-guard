"""Create owned conversations without changing existing document artifacts."""

import sqlalchemy as sa
from alembic import op

revision = "0009_chat_threads"
down_revision = "0008_pdf_ingestion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add conversation storage and immutable ownership."""
    op.create_table(
        "chat_threads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), server_default="Новый диалог", nullable=False),
        sa.Column("revision", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_chat_threads"),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_chat_threads_owner_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("revision >= 0", name="ck_chat_threads_revision"),
    )
    op.create_index(
        "ix_chat_threads_owner_created", "chat_threads", ["owner_user_id", "created_at", "id"]
    )
    op.create_table(
        "chat_turns",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("request_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("expected_thread_revision", sa.BigInteger(), nullable=False),
        sa.Column("user_input", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), server_default="pending", nullable=False),
        sa.Column("ordinal", sa.BigInteger(), nullable=True),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column(
            "provenance_complete", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "server_generated_neutral",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("neutral_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_chat_turns"),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["chat_threads.id"],
            name="fk_chat_turns_thread_id_chat_threads",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("thread_id", "request_id", name="uq_chat_turns_request"),
        sa.UniqueConstraint("thread_id", "ordinal", name="uq_chat_turns_ordinal"),
        sa.CheckConstraint("octet_length(request_sha256) = 32", name="ck_chat_turns_request_hash"),
        sa.CheckConstraint("expected_thread_revision >= 0", name="ck_chat_turns_revision"),
        sa.CheckConstraint(
            """(
            (state='pending' AND answer IS NULL AND completed_at IS NULL AND ordinal IS NULL
             AND lease_expires_at IS NOT NULL AND NOT provenance_complete
             AND NOT server_generated_neutral AND neutral_reason IS NULL)
            OR (state='available' AND answer IS NOT NULL AND completed_at IS NOT NULL
             AND ordinal IS NOT NULL AND ordinal > 0 AND provenance_complete
             AND NOT server_generated_neutral AND neutral_reason IS NULL)
            OR (state='neutral' AND answer IS NULL AND completed_at IS NOT NULL
             AND ordinal IS NOT NULL AND ordinal > 0 AND NOT provenance_complete
             AND server_generated_neutral AND neutral_reason IN
             ('no_context','generation_unavailable','policy_changed','interrupted'))
            ) IS TRUE""",
            name="ck_chat_turns_state",
        ),
    )
    op.create_index(
        "uq_chat_turns_pending",
        "chat_turns",
        ["thread_id"],
        unique=True,
        postgresql_where=sa.text("state = 'pending'"),
    )
    op.create_table(
        "turn_sources",
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint(
            "turn_id", "document_id", "document_version_id", "chunk_id", name="pk_turn_sources"
        ),
        sa.ForeignKeyConstraint(
            ["turn_id"],
            ["chat_turns.id"],
            name="fk_turn_sources_turn_id_chat_turns",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "document_version_id", "chunk_id"],
            [
                "document_chunks.document_id",
                "document_chunks.document_version_id",
                "document_chunks.id",
            ],
            name="fk_turn_sources_chunk",
            ondelete="RESTRICT",
        ),
    )
    op.execute(
        sa.text("""CREATE FUNCTION preserve_thread_owner() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
        IF NEW.owner_user_id IS DISTINCT FROM OLD.owner_user_id THEN
            RAISE EXCEPTION 'Thread owner is immutable' USING ERRCODE='23514';
        END IF; RETURN NEW; END $$""")
    )
    op.execute(
        sa.text("""CREATE TRIGGER chat_thread_owner_immutable
        BEFORE UPDATE ON chat_threads FOR EACH ROW EXECUTE FUNCTION preserve_thread_owner()""")
    )
    op.execute(
        sa.text("""CREATE FUNCTION ensure_turn_sources(target uuid) RETURNS void
        LANGUAGE plpgsql AS $$ DECLARE current_state text; BEGIN
        SELECT state INTO current_state FROM chat_turns WHERE id=target FOR UPDATE;
        IF NOT FOUND THEN RETURN; END IF;
        IF (current_state='available') IS DISTINCT FROM
           EXISTS(SELECT 1 FROM turn_sources WHERE turn_id=target) THEN
            RAISE EXCEPTION 'Invalid turn sources' USING ERRCODE='23514';
        END IF; END $$""")
    )
    op.execute(
        sa.text("""CREATE FUNCTION check_chat_turn_sources() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
        PERFORM ensure_turn_sources(NEW.id); RETURN NULL;
        END $$""")
    )
    op.execute(
        sa.text("""CREATE FUNCTION check_source_turn_state() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
        IF TG_OP IN ('DELETE','UPDATE') THEN
            PERFORM ensure_turn_sources(OLD.turn_id);
        END IF;
        IF TG_OP IN ('INSERT','UPDATE') THEN
            PERFORM ensure_turn_sources(NEW.turn_id);
        END IF; RETURN NULL; END $$""")
    )
    op.execute(
        sa.text("""CREATE CONSTRAINT TRIGGER chat_turn_sources_consistent
        AFTER INSERT OR UPDATE ON chat_turns DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION check_chat_turn_sources()""")
    )
    op.execute(
        sa.text("""CREATE CONSTRAINT TRIGGER source_turn_state_consistent
        AFTER INSERT OR UPDATE OR DELETE ON turn_sources DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION check_source_turn_state()""")
    )


def downgrade() -> None:
    """Only remove unused chat storage; never discard conversation history."""
    op.execute(sa.text("LOCK TABLE chat_threads IN ACCESS EXCLUSIVE MODE"))
    op.execute(
        sa.text("""DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM chat_threads) THEN
            RAISE EXCEPTION 'Chat downgrade requires empty conversations';
        END IF; END $$""")
    )
    op.drop_table("turn_sources")
    op.drop_table("chat_turns")
    op.drop_table("chat_threads")
    op.execute(sa.text("DROP FUNCTION check_source_turn_state()"))
    op.execute(sa.text("DROP FUNCTION check_chat_turn_sources()"))
    op.execute(sa.text("DROP FUNCTION ensure_turn_sources(uuid)"))
    op.execute(sa.text("DROP FUNCTION preserve_thread_owner()"))
