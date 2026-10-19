from collections.abc import Iterable, Sequence

from sqlalchemy import delete, func, select, tuple_
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


def _set_columns(obj) -> dict:
    """The attributes a parser set on a model object, as a row dict.

    One it never set (such as `imdb_score`) is left out, so an upsert cannot overwrite another
    pipeline's value, while one it set to None is written as NULL.
    """
    return {name: value for name, value in obj.__dict__.items() if not name.startswith("_")}


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

    async def existing_anime_ids(self, ids: Iterable[int]) -> set[int]:
        """Which of these anime ids have a row, in one query. Meant for up to a few thousand ids."""
        ids = set(ids)
        if not ids:
            return set()
        result = await self.db.execute(select(Anime.mal_id).where(Anime.mal_id.in_(ids)))
        return set(result.scalars().all())

    async def get_synopsis_hash(self, mal_id: int) -> str | None:
        """The stored synopsis hash, or None if the anime or its hash is missing."""
        result = await self.db.execute(select(Anime.synopsis_hash).where(Anime.mal_id == mal_id))
        return result.scalar_one_or_none()

    async def upsert_anime(self, animes: Sequence[Anime]) -> int:
        """Upsert the Anime objects the parser built, in bulk.

        Returns how many rows were inserted or changed: an anime whose stored values already
        match is left untouched, so `updated_at` means "last time the data changed".

        Only the attributes the parser set are written. An attribute it never set (such as
        `imdb_score`) is left out, so another pipeline's value is not overwritten, while one it
        set to None is written as NULL.
        """
        rows = [_set_columns(anime) for anime in animes]
        return await self._upsert(Anime, rows, only_if_changed=True)

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

    async def upsert_statistics(self, statistics: Sequence[AnimeStatisticsMal]) -> int:
        """Upsert the statistics rows the parser built, in bulk; returns rows written."""
        return await self._upsert(AnimeStatisticsMal, [_set_columns(row) for row in statistics])

    async def upsert_episodes(self, episodes: Sequence[Episode]) -> int:
        """Upsert the Episode objects the parser built, in bulk; returns rows written.

        MAL's synopsis wins when it has one, and a MAL episode with no synopsis keeps whatever is
        stored (e.g. one an IMDb pipeline filled in later), so a refresh cannot wipe it.
        """
        return await self._upsert(
            Episode,
            [_set_columns(episode) for episode in episodes],
            keep_when_new_is_null=("synopsis", "synopsis_hash"),
        )

    async def upsert_reviews(self, reviews: Sequence[Review]) -> int:
        """Upsert the Review objects the parser built, in bulk, on the review's own mal_id.

        Reviews are added and updated, never deleted, and `updated_at` moves on every upsert
        because it means "last seen" (ADR 0014).
        """
        return await self._upsert(Review, [_set_columns(review) for review in reviews])

    # --- transaction -----------------------------------------------------------------------

    async def commit(self) -> None:
        await self.db.commit()

    async def rollback(self) -> None:
        await self.db.rollback()

    # --- helpers ---------------------------------------------------------------------------

    async def _upsert(
        self,
        model,
        rows: Sequence[dict],
        only_if_changed: bool = False,
        keep_when_new_is_null: Sequence[str] = (),
    ) -> int:
        """Insert the rows; where one with the same primary key exists, update it instead.

        Only the columns in the row dicts are written. With `only_if_changed`, an existing row
        is updated only if one of those columns differs (NULLs compare correctly), so identical
        rows cause no write. For the columns in `keep_when_new_is_null`, a NULL in the new row
        keeps the stored value instead of overwriting it. Returns how many rows were inserted or
        updated.
        """
        if not rows:
            return 0
        keys = [column.name for column in model.__table__.primary_key.columns]
        written = 0
        # In chunks: Postgres allows ~32,000 parameters per statement, and a long series can
        # have thousands of episodes.
        for start in range(0, len(rows), ROWS_PER_STATEMENT):
            stmt = insert(model).values(rows[start : start + ROWS_PER_STATEMENT])
            columns = [column for column in rows[0] if column not in keys]
            update = {column: stmt.excluded[column] for column in columns}
            for column in keep_when_new_is_null:
                if column in update:
                    update[column] = func.coalesce(stmt.excluded[column], getattr(model, column))
            if "updated_at" in model.__table__.c:
                update["updated_at"] = func.now()  # onupdate does not apply to upserts
            differs = None
            if only_if_changed:
                differs = tuple_(*(getattr(model, c) for c in columns)).is_distinct_from(
                    tuple_(*(stmt.excluded[c] for c in columns))
                )
            stmt = stmt.on_conflict_do_update(index_elements=keys, set_=update, where=differs)
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
