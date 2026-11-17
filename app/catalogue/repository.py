from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import case, delete, func, select, tuple_
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


@dataclass(frozen=True)
class CohortStanding:
    """Where one anime stands among the anime of the same `type` and `year`.

    `cohort_size` counts every anime in the cohort. Each metric is ranked only among the
    cohort rows that have a value for it, and `*_count` says how many those are, so the caller
    can decide whether a cohort is too small. A metric is None when this anime has no value for
    it. Ranks start at 1 for the best; a percentile is the share of the cohort the anime beats
    (0-1, higher is better) and follows the metric's own direction (a low popularity rank is
    good).
    """

    cohort_size: int
    score_count: int
    score_rank: int | None
    score_percentile: float | None
    popularity_count: int
    popularity_rank: int | None
    popularity_percentile: float | None
    favorites_count: int
    favorites_rank: int | None
    favorites_percentile: float | None


def _as_float(value) -> float | None:
    return None if value is None else float(value)


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

    async def anime_ids_with_status(self, statuses: Iterable[str]) -> set[int]:
        """The ids of the anime whose stored status is one of `statuses`, e.g. "Currently Airing".

        These are MAL's own status texts as the parser stored them, not the `airing` / `upcoming`
        words the Tenrai list endpoint takes.
        """
        statuses = list(statuses)
        if not statuses:
            return set()
        result = await self.db.execute(select(Anime.mal_id).where(Anime.status.in_(statuses)))
        return set(result.scalars().all())

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

    # --- link tables: replaced per anime, in bulk, only when they differ ---------------------

    async def replace_anime_tags(self, tag_ids_by_anime: Mapping[int, Iterable[int]]) -> int:
        """Make each anime's tags exactly its list of tag ids. The anime and tags must exist.

        An anime mapped to an empty list has its tags cleared. Returns how many anime had
        their links changed; one whose stored links already match is left untouched.
        """
        wanted = {
            anime_id: {(tag_id,) for tag_id in tag_ids}
            for anime_id, tag_ids in tag_ids_by_anime.items()
        }
        return await self._replace_links(AnimeTag, ("tag_id",), wanted)

    async def replace_anime_studios(self, studio_ids_by_anime: Mapping[int, Iterable[int]]) -> int:
        wanted = {
            anime_id: {(studio_id,) for studio_id in studio_ids}
            for anime_id, studio_ids in studio_ids_by_anime.items()
        }
        return await self._replace_links(AnimeStudio, ("studio_id",), wanted)

    async def replace_relations(
        self, relations_by_anime: Mapping[int, Iterable[tuple[str, int]]]
    ) -> int:
        """Make each anime's relations exactly its (relation name, related mal_id) pairs."""
        wanted = {
            anime_id: {(relation, related_id) for relation, related_id in relations}
            for anime_id, relations in relations_by_anime.items()
        }
        return await self._replace_links(AnimeRelation, ("relation", "related_mal_id"), wanted)

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

    # --- reads for the statistics endpoint ---------------------------------------------------

    async def get_anime_with_statistics(
        self, mal_id: int
    ) -> tuple[Anime, AnimeStatisticsMal | None] | None:
        """The anime and its statistics row (None if it has none), or None if no such anime."""
        result = await self.db.execute(
            select(Anime, AnimeStatisticsMal)
            .outerjoin(AnimeStatisticsMal, AnimeStatisticsMal.anime_id == Anime.mal_id)
            .where(Anime.mal_id == mal_id)
        )
        row = result.one_or_none()
        return None if row is None else (row[0], row[1])

    async def get_tags_of_anime(self, mal_id: int) -> list[Tag]:
        result = await self.db.execute(
            select(Tag)
            .join(AnimeTag, AnimeTag.tag_id == Tag.mal_id)
            .where(AnimeTag.anime_id == mal_id)
            .order_by(Tag.name)
        )
        return list(result.scalars().all())

    async def get_studios_of_anime(self, mal_id: int) -> list[Studio]:
        result = await self.db.execute(
            select(Studio)
            .join(AnimeStudio, AnimeStudio.studio_id == Studio.mal_id)
            .where(AnimeStudio.anime_id == mal_id)
            .order_by(Studio.name)
        )
        return list(result.scalars().all())

    async def get_reviews_of_anime(self, mal_id: int) -> list[Review]:
        """Every stored review, most helpful first (reviews without a count go last)."""
        result = await self.db.execute(
            select(Review)
            .where(Review.anime_id == mal_id)
            .order_by(Review.reaction_overall.desc().nulls_last(), Review.mal_id)
        )
        return list(result.scalars().all())

    async def get_cohort_standing(
        self, mal_id: int, type_: str | None, year: int | None
    ) -> CohortStanding | None:
        """Rank one anime among those with the same `type` and `year`, in one query.

        Returns None if the anime has no type or year (so no cohort) or does not exist. Score
        and favorites per member rank high to low; popularity ranks by MAL's popularity rank
        (1 is the most popular). Each metric is ranked only among cohort rows that have a value:
        every window is partitioned on "value is missing", so the missing rows form their own
        partition and never shift the others' ranks or percentiles.
        """
        if type_ is None or year is None:
            return None

        favorites_per_member = Anime.favorites_mal * 1.0 / func.nullif(Anime.members_mal, 0)
        score_missing = Anime.mal_score.is_(None)
        popularity_missing = Anime.popularity_mal.is_(None)
        favorites_missing = favorites_per_member.is_(None)

        cohort = (
            select(
                Anime.mal_id.label("mal_id"),
                func.count().over().label("cohort_size"),
                func.count(Anime.mal_score).over().label("score_count"),
                func.count(Anime.popularity_mal).over().label("popularity_count"),
                func.count(favorites_per_member).over().label("favorites_count"),
                score_missing.label("score_missing"),
                popularity_missing.label("popularity_missing"),
                favorites_missing.label("favorites_missing"),
                func.rank()
                .over(partition_by=score_missing, order_by=Anime.mal_score.desc())
                .label("score_rank"),
                func.percent_rank()
                .over(partition_by=score_missing, order_by=Anime.mal_score.asc())
                .label("score_percentile"),
                func.rank()
                .over(partition_by=popularity_missing, order_by=Anime.popularity_mal.asc())
                .label("popularity_rank"),
                # A bigger popularity rank number means less popular, so sort descending: the
                # most popular anime comes last and gets the highest percentile.
                func.percent_rank()
                .over(partition_by=popularity_missing, order_by=Anime.popularity_mal.desc())
                .label("popularity_percentile"),
                func.rank()
                .over(partition_by=favorites_missing, order_by=favorites_per_member.desc())
                .label("favorites_rank"),
                func.percent_rank()
                .over(partition_by=favorites_missing, order_by=favorites_per_member.asc())
                .label("favorites_percentile"),
            )
            .where(Anime.type == type_, Anime.year == year)
            .cte("cohort")
        )

        def unless_missing(missing, value):
            return case((missing, None), else_=value)

        result = await self.db.execute(
            select(
                cohort.c.cohort_size,
                cohort.c.score_count,
                cohort.c.popularity_count,
                cohort.c.favorites_count,
                unless_missing(cohort.c.score_missing, cohort.c.score_rank),
                unless_missing(cohort.c.score_missing, cohort.c.score_percentile),
                unless_missing(cohort.c.popularity_missing, cohort.c.popularity_rank),
                unless_missing(cohort.c.popularity_missing, cohort.c.popularity_percentile),
                unless_missing(cohort.c.favorites_missing, cohort.c.favorites_rank),
                unless_missing(cohort.c.favorites_missing, cohort.c.favorites_percentile),
            ).where(cohort.c.mal_id == mal_id)
        )
        row = result.one_or_none()
        if row is None:
            return None
        (
            cohort_size,
            score_count,
            popularity_count,
            favorites_count,
            score_rank,
            score_percentile,
            popularity_rank,
            popularity_percentile,
            favorites_rank,
            favorites_percentile,
        ) = row
        return CohortStanding(
            cohort_size=cohort_size,
            score_count=score_count,
            score_rank=score_rank,
            score_percentile=_as_float(score_percentile),
            popularity_count=popularity_count,
            popularity_rank=popularity_rank,
            popularity_percentile=_as_float(popularity_percentile),
            favorites_count=favorites_count,
            favorites_rank=favorites_rank,
            favorites_percentile=_as_float(favorites_percentile),
        )

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
        self, model, columns: tuple[str, ...], wanted: dict[int, set[tuple]]
    ) -> int:
        """Replace the rows of several anime in a link table; returns how many anime changed.

        One query reads the stored links of all of them, the comparison is done in memory, and
        only the anime whose links differ are deleted and re-inserted.
        """
        if not wanted:
            return 0
        selected = [getattr(model, c) for c in columns]
        result = await self.db.execute(
            select(model.anime_id, *selected).where(model.anime_id.in_(list(wanted)))
        )
        current: dict[int, set[tuple]] = defaultdict(set)
        for anime_id, *link in result.all():
            current[anime_id].add(tuple(link))

        changed = [anime_id for anime_id in wanted if current[anime_id] != wanted[anime_id]]
        if not changed:
            return 0
        await self.db.execute(delete(model).where(model.anime_id.in_(changed)))
        rows = [
            {"anime_id": anime_id, **dict(zip(columns, link, strict=True))}
            for anime_id in changed
            for link in sorted(wanted[anime_id])
        ]
        for start in range(0, len(rows), ROWS_PER_STATEMENT):
            await self.db.execute(insert(model).values(rows[start : start + ROWS_PER_STATEMENT]))
        return len(changed)
