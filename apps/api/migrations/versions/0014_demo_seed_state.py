"""Add owned demo metadata without changing any production account or policy row."""

import sqlalchemy as sa
from alembic import op

revision = "0014_demo_seed_state"
down_revision = "0013_document_origin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create an empty singleton marker and per-instance completed-step ledger."""
    _ = op.create_table(
        "demo_seed_state",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=False),
        sa.Column("instance_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.String(100), nullable=False),
        sa.Column("database_name", sa.String(63), nullable=False),
        sa.Column("database_role", sa.String(63), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("instance_id"),
        sa.CheckConstraint("id = 1", name="ck_demo_seed_state_singleton"),
    )
    _ = op.create_table(
        "demo_seed_steps",
        sa.Column("instance_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(512), nullable=False),
        sa.Column("object_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=True),
        sa.Column("upload_sha256", sa.CHAR(64), nullable=True),
        sa.Column("origin_sha256", sa.CHAR(64), nullable=True),
        sa.Column("definition_sha256", sa.CHAR(64), nullable=True),
        sa.PrimaryKeyConstraint("instance_id", "key"),
        sa.ForeignKeyConstraint(
            ["instance_id"], ["demo_seed_state.instance_id"], ondelete="RESTRICT"
        ),
    )


def downgrade() -> None:
    """Refuse dropping populated ownership metadata outside empty disposable schemas."""
    op.execute("LOCK TABLE demo_seed_state IN ACCESS EXCLUSIVE MODE")
    op.execute(
        """DO $$ BEGIN IF EXISTS (SELECT 1 FROM demo_seed_state)
        THEN RAISE EXCEPTION 'demo metadata is populated'; END IF; END $$"""
    )
    op.drop_table("demo_seed_steps")
    op.drop_table("demo_seed_state")
