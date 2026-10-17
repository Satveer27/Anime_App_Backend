"""deleted related_type

anime_relation holds only anime-to-anime edges now, so the target type is no longer
part of the key. Manga edges stay in raw_tenrai and can be re-parsed later.

Revision ID: 84b981b0d01a
Revises: 78f3013b67fa
Create Date: 2026-10-17 04:51:27.123551

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "84b981b0d01a"
down_revision: str | Sequence[str] | None = "78f3013b67fa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("DELETE FROM anime_relation WHERE related_type <> 'anime'")
    op.drop_constraint("anime_relation_pkey", "anime_relation", type_="primary")
    op.drop_column("anime_relation", "related_type")
    op.create_primary_key(
        "anime_relation_pkey", "anime_relation", ["anime_id", "relation", "related_mal_id"]
    )


def downgrade() -> None:
    """Downgrade schema. Deleted manga edges are not restored; the rest become 'anime'."""
    op.drop_constraint("anime_relation_pkey", "anime_relation", type_="primary")
    op.add_column(
        "anime_relation",
        sa.Column("related_type", sa.Text(), nullable=False, server_default="anime"),
    )
    op.alter_column("anime_relation", "related_type", server_default=None)
    op.create_primary_key(
        "anime_relation_pkey",
        "anime_relation",
        ["anime_id", "relation", "related_type", "related_mal_id"],
    )
