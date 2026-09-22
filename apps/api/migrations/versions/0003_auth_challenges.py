"""Add one-use pre-auth challenges and bounded authentication counters."""

import sqlalchemy as sa
from alembic import op

revision = "0003_auth_challenges"
down_revision = "0002_access_control"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create digest-only authentication preflight state."""
    op.create_table(
        "auth_challenges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("token_digest", sa.LargeBinary(), nullable=False),
        sa.Column("csrf_token_digest", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_auth_challenges"),
        sa.UniqueConstraint("token_digest", name="uq_auth_challenges_token_digest"),
        sa.CheckConstraint(
            "octet_length(token_digest) = 32", name="ck_auth_challenges_token_length"
        ),
        sa.CheckConstraint(
            "octet_length(csrf_token_digest) = 32", name="ck_auth_challenges_csrf_length"
        ),
        sa.CheckConstraint(
            "expires_at = created_at + interval '10 minutes'", name="ck_auth_challenges_expiry"
        ),
        sa.CheckConstraint("consumed_at >= created_at", name="ck_auth_challenges_consumed_order"),
    )
    op.create_index("ix_auth_challenges_expires_at", "auth_challenges", ["expires_at"])
    op.create_table(
        "auth_rate_buckets",
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("key_digest", sa.LargeBinary(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("kind", "key_digest", "window_start", name="pk_auth_rate_buckets"),
        sa.CheckConstraint("octet_length(key_digest) = 32", name="ck_auth_rate_buckets_key_length"),
        sa.CheckConstraint("attempts >= 1", name="ck_auth_rate_buckets_attempts_positive"),
    )


def downgrade() -> None:
    """Remove only authentication preflight state."""
    op.drop_table("auth_rate_buckets")
    op.drop_index("ix_auth_challenges_expires_at", table_name="auth_challenges")
    op.drop_table("auth_challenges")
