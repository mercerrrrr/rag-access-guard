"""Bind stored answers to their complete canonical source set."""

import sqlalchemy as sa
from alembic import op

revision = "0011_history_closure"
down_revision = "0010_source_order"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Snapshot trusted existing closures; old writers without a witness fail closed."""
    op.execute(sa.text("LOCK TABLE chat_turns, turn_sources IN ACCESS EXCLUSIVE MODE"))
    op.add_column("chat_turns", sa.Column("source_closure_sha256", sa.LargeBinary(), nullable=True))
    op.create_check_constraint(
        "ck_chat_turns_source_closure", "chat_turns", "octet_length(source_closure_sha256) = 32"
    )
    op.execute(
        sa.text("""UPDATE chat_turns AS turns SET source_closure_sha256=sources.digest
        FROM (SELECT turn_id, sha256(convert_to(string_agg(
            document_id::text || '/' || document_version_id::text || '/' || chunk_id::text,
            E'\\n' ORDER BY document_id, document_version_id, chunk_id), 'UTF8')) AS digest
            FROM turn_sources GROUP BY turn_id) AS sources
        WHERE turns.id=sources.turn_id AND turns.state='available'
          AND turns.provenance_complete""")
    )


def downgrade() -> None:
    """Do not discard a persisted closure witness during rollback."""
    op.execute(sa.text("LOCK TABLE chat_turns, turn_sources IN ACCESS EXCLUSIVE MODE"))
    op.execute(
        sa.text("""DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM chat_turns WHERE source_closure_sha256 IS NOT NULL) THEN
            RAISE EXCEPTION 'History downgrade requires no witnessed answers';
        END IF; END $$""")
    )
    op.drop_constraint("ck_chat_turns_source_closure", "chat_turns", type_="check")
    op.drop_column("chat_turns", "source_closure_sha256")
