"""add anime title search trigram index

The anime list searches the title, the English title and the Japanese title for the start of any
word. On the 23,086-anime table that scanned every row (105 ms for one search, run twice per
request). A GIN trigram index on the combined title text answers it in 0.3 to 2 ms (see
docs/METRICS.md section 10 and ADR 0015).

The index is on an expression, which SQLAlchemy models do not declare, so it exists only here.
`app.catalogue.read_repository.SEARCH_TEXT_SQL` must stay identical to SEARCH_TEXT_SQL below, or
the search stops using the index (a test compares the two).

Revision ID: d4a9c2e7f1b8
Revises: b3d8f50e6c17
Create Date: 2026-11-18 10:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4a9c2e7f1b8"
down_revision: str | Sequence[str] | None = "b3d8f50e6c17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEARCH_TEXT_SQL = (
    "(' ' || coalesce(title, '') || ' ' || chr(31) || ' ' || coalesce(title_english, '')"
    " || ' ' || chr(31) || ' ' || coalesce(title_japanese, ''))"
)


def upgrade() -> None:
    # pg_trgm is a trusted extension, so the database owner can create it.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        f"CREATE INDEX ix_anime_search_trgm ON anime USING gin ({SEARCH_TEXT_SQL} gin_trgm_ops)"
    )


def downgrade() -> None:
    op.drop_index("ix_anime_search_trgm", table_name="anime")
    # The extension is left installed: dropping it could break anything else that uses it.
