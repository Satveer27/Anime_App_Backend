"""add anime members and score sort indexes

The anime list orders by members or score, highest first, with the id as the tie-break. Measured
on temporary copies of the table: the page query went from about 20 ms (read and sort every
row) to 0.07 ms. See docs/METRICS.md section 10 and ADR 0015.

Revision ID: e5b0d3f8a2c9
Revises: d4a9c2e7f1b8
Create Date: 2026-11-18 10:05:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5b0d3f8a2c9"
down_revision: str | Sequence[str] | None = "d4a9c2e7f1b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_anime_members_mal",
        "anime",
        [sa.text("members_mal DESC NULLS LAST"), "mal_id"],
        unique=False,
    )
    op.create_index(
        "ix_anime_mal_score",
        "anime",
        [sa.text("mal_score DESC NULLS LAST"), "mal_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_anime_mal_score", table_name="anime")
    op.drop_index("ix_anime_members_mal", table_name="anime")
