from collections.abc import Iterable, Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalogue.models import (
    Anime,
    AnimeRelation,
    AnimeStatisticsMal,
    AnimeStudio,
    AnimeTag,
    Episode,
    Review,
    Studio,
    Tag,
)

ROWS_PER_STATEMENT = 500


class CatalogueRepository:
    """Reads and idempotent writes for the anime catalogue (ADR 0014).

    An upsert inserts a row, or updates it if a row with the same primary key exists. It writes
    only the columns in the dict it is given, so one source's pipeline never overwrites
    another's columns (e.g. `imdb_score`). The parent `anime` row must exist before statistics,
    episodes, reviews or any link rows are written. Nothing here commits: the caller owns the
    transaction.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    # --- anime ---------------------------------------------------------------------------

    async def anime_exists(self, mal_id: int) -> bool:
        result = await self.db.execute(select(Anime.mal_id).where(Anime.mal_id == mal_id))
        return result.scalar_one_or_none() is not None

    async def get_synopsis_hash(self, mal_id: int) -> str | None:
        """The stored synopsis hash, or None if the anime or its hash is missing."""
        result = await self.db.execute(select(Anime.synopsis_hash).where(Anime.mal_id == mal_id))
        return result.scalar_one_or_none()

    async def upsert_anime(self, row: dict) -> None:
        await self._upsert(Anime, [row])

    # --- tags and studios ----------------------------------------------------------------

    async def get_tags(self) -> dict[int, str]:
        """Every tag as {mal_id: name}, to be loaded once per run"""
        result = await self.db.execute(select(Tag.mal_id, Tag.name))
        return {mal_id: name for mal_id, name in result.all()}

    async def upsert_tags(self, tags: Iterable[tuple[int, str]]) -> int:
        """Insert new tags and rename changed ones. Pass only those that are new or renamed."""
        rows = [{"mal_id": mal_id, "name": name} for mal_id, name in tags]
        return await self._upsert(Tag, rows)

    async def get_studios(self) -> dict[int, str]:
        result = await self.db.execute(select(Studio.mal_id, Studio.name))
        return {mal_id: name for mal_id, name in result.all()}

    async def upsert_studios(self, studios: Iterable[tuple[int, str]]) -> int:
        rows = [{"mal_id": mal_id, "name": name} for mal_id, name in studios]
        return await self._upsert(Studio, rows)

    # --- link tables: replaced per anime, only when they differ ---------------------------

    async def replace_anime_tags(self, anime_id: int, tag_ids: Iterable[int]) -> bool:
        """Make the anime's tags exactly `tag_ids`. The tags must already exist.

        Returns True if rows were written, False if the stored links already matched.
        """
        wanted = {(tag_id,) for tag_id in tag_ids}
        return await self._replace_links(AnimeTag, anime_id, ("tag_id",), wanted)

    async def replace_anime_studios(self, anime_id: int, studio_ids: Iterable[int]) -> bool:
        wanted = {(studio_id,) for studio_id in studio_ids}
        return await self._replace_links(AnimeStudio, anime_id, ("studio_id",), wanted)

    async def replace_relations(self, anime_id: int, relations: Iterable[tuple[str, int]]) -> bool:
        """Make the anime's relations exactly these (relation name, related mal_id) pairs."""
        wanted = {(relation, related_id) for relation, related_id in relations}
        return await self._replace_links(
            AnimeRelation, anime_id, ("relation", "related_mal_id"), wanted
        )

    # --- one-to-many rows ------------------------------------------------------------------

    async def upsert_statistics(self, row: dict) -> None:
        await self._upsert(AnimeStatisticsMal, [row])

    async def upsert_episodes(self, rows: Sequence[dict]) -> int:
        return await self._upsert(Episode, rows)

    async def upsert_reviews(self, rows: Sequence[dict]) -> int:
        """Upsert on the review's own mal_id. Reviews are added and updated, never deleted."""
        return await self._upsert(Review, rows)

    # --- transaction -----------------------------------------------------------------------

    async def commit(self) -> None:
        await self.db.commit()

    async def rollback(self) -> None:
        await self.db.rollback()

    # --- helpers ---------------------------------------------------------------------------

    async def _upsert(self, model, rows: Sequence[dict]) -> int:
        """Insert the rows; where one with the same primary key exists, update it instead.

        Only the columns in the row dicts are written. Returns how many rows were written.
        """
        if not rows:
            return 0
        keys = [column.name for column in model.__table__.primary_key.columns]
        written = 0
        # In chunks: Postgres allows ~32,000 parameters per statement, and a long series can
        # have thousands of episodes.
        for start in range(0, len(rows), ROWS_PER_STATEMENT):
            stmt = insert(model).values(rows[start : start + ROWS_PER_STATEMENT])
            update = {column: stmt.excluded[column] for column in rows[0] if column not in keys}
            if "updated_at" in model.__table__.c:
                update["updated_at"] = func.now()  # onupdate does not apply to upserts
            stmt = stmt.on_conflict_do_update(index_elements=keys, set_=update)
            written += (await self.db.execute(stmt)).rowcount
        return written

    async def _replace_links(
        self, model, anime_id: int, columns: tuple[str, ...], wanted: set[tuple]
    ) -> bool:
        """Replace an anime's rows in a link table, writing nothing if they already match."""
        selected = [getattr(model, c) for c in columns]
        result = await self.db.execute(select(*selected).where(model.anime_id == anime_id))
        if {tuple(row) for row in result.all()} == wanted:
            return False
        await self.db.execute(delete(model).where(model.anime_id == anime_id))
        if wanted:
            await self.db.execute(
                insert(model).values(
                    [
                        {"anime_id": anime_id, **dict(zip(columns, link, strict=True))}
                        for link in sorted(wanted)
                    ]
                )
            )
        return True
