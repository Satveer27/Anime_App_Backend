"""add revoked_at to refresh_token

Revision ID: b7a41c9e2d10
Revises: 9d76c03afd2b
Create Date: 2026-10-16 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7a41c9e2d10"
down_revision: str | Sequence[str] | None = "9d76c03afd2b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "refresh_token",
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("refresh_token", "revoked_at")
