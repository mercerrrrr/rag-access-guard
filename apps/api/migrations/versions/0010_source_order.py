"""Preserve source order for new answers without rewriting legacy provenance."""

import sqlalchemy as sa
from alembic import op

revision = "0010_source_order"
down_revision = "0009_chat_threads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Legacy rows and concurrent old writers retain an unknown position."""
    op.add_column("turn_sources", sa.Column("position", sa.Integer(), nullable=True))
    op.create_check_constraint("ck_turn_sources_position", "turn_sources", "position >= 0")
    op.create_unique_constraint("uq_turn_sources_position", "turn_sources", ["turn_id", "position"])


def downgrade() -> None:
    """Refuse to discard recorded order; legacy-only storage is reversible."""
    op.execute(sa.text("LOCK TABLE turn_sources IN ACCESS EXCLUSIVE MODE"))
    op.execute(
        sa.text("""DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM turn_sources WHERE position IS NOT NULL) THEN
            RAISE EXCEPTION 'Source downgrade requires unpositioned sources';
        END IF; END $$""")
    )
    op.drop_constraint("uq_turn_sources_position", "turn_sources", type_="unique")
    op.drop_constraint("ck_turn_sources_position", "turn_sources", type_="check")
    op.drop_column("turn_sources", "position")
