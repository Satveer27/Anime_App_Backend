"""add anime (type, year) index

The cohort percentile query selects every anime of one type and year, which scanned the whole
anime table (11.5 ms). Measured with the index: 2.1 to 3.8 ms. See docs/METRICS.md section 7
and ADR 0015.

Revision ID: b3d8f50e6c17
Revises: a7c2e41d9b03
Create Date: 2026-11-17 19:11:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3d8f50e6c17"
down_revision: str | Sequence[str] | None = "a7c2e41d9b03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_anime_type_year", "anime", ["type", "year"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_anime_type_year", table_name="anime")
