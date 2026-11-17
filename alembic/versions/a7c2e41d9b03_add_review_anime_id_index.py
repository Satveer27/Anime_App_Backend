"""add review (anime_id) index

The statistics endpoint lists the reviews of one anime, which scanned all 87,493 review rows
(23.1 ms). Measured with the index: 0.06 ms. See docs/METRICS.md section 7 and ADR 0015.

Revision ID: a7c2e41d9b03
Revises: 94794cdb7738
Create Date: 2026-11-17 19:10:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c2e41d9b03"
down_revision: str | Sequence[str] | None = "94794cdb7738"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_review_anime_id", "review", ["anime_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_review_anime_id", table_name="review")
