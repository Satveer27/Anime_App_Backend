"""add episode (aired) index

The anime list can filter by whether an anime released an episode in the last 10 days
(`release_status=releasing`), which scanned all 112,257 episodes (about 28 ms per statement).
Measured with the index on a temporary copy: 0.15 ms. See docs/METRICS.md section 10 and ADR 0015.

Revision ID: f6c1e4a9b3d0
Revises: e5b0d3f8a2c9
Create Date: 2026-11-18 14:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6c1e4a9b3d0"
down_revision: str | Sequence[str] | None = "e5b0d3f8a2c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_episode_aired", "episode", ["aired"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_episode_aired", table_name="episode")
