"""Enable the required pgvector extension.

Revision ID: 0001_pgvector
Revises:
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001_pgvector"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Enable pgvector at the version required by the application."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector VERSION '0.8.6'")


def downgrade() -> None:
    """Remove pgvector without cascading into dependent objects."""
    op.execute("DROP EXTENSION IF EXISTS vector RESTRICT")
