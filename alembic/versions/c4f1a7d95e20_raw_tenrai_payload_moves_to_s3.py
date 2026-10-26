"""raw_tenrai payload moves to S3

The response now lives in an S3 object (ADR 0016); the row keeps only its size.

Rows written before this migration lose their payload, so their objects do not exist. Empty
raw_tenrai and ingestion_task (or re-fetch) after upgrading a database that already holds data.

Revision ID: c4f1a7d95e20
Revises: 3bd5db36b875
Create Date: 2026-10-22 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4f1a7d95e20"
down_revision: str | Sequence[str] | None = "3bd5db36b875"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "raw_tenrai", sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0")
    )
    op.alter_column("raw_tenrai", "size_bytes", server_default=None)
    op.drop_column("raw_tenrai", "payload")


def downgrade() -> None:
    op.add_column(
        "raw_tenrai",
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )
    op.alter_column("raw_tenrai", "payload", server_default=None)
    op.drop_column("raw_tenrai", "size_bytes")
