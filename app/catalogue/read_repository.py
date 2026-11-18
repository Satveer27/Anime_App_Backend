from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog
from sqlalchemy import Text, and_, case, exists, func, literal_column, or_, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalogue.enums import AnimeSort, GroupSort, Order, ReleaseStatus, StudioSort
from app.catalogue.models import (
    SEARCH_TEXT_SQL,
    STATUS_AIRING,
    Anime,
    AnimeStudio,
    AnimeTag,
    Episode,
    Studio,
    Tag,
)
from app.exceptions import ServiceUnavailableError

logger = structlog.get_logger()

# Every public list and aggregate query runs under this limit. A query that passes it is
# cancelled by Postgres and the caller answers 503 (see `is_statement_timeout`).
STATEMENT_TIMEOUT_MS = 5000
STATEMENT_TIMEOUT_SQLSTATE = "57014"  # Postgres: query_canceled

# `release_status=releasing` means an episode aired in this many days. A weekly show is never
# silent for longer than a week, and the extra days absorb a slipped broadcast.
RELEASING_WITHIN_DAYS = 10

LIKE_ESCAPE = "\\"
SEASON_ORDER = {"winter": 0, "spring": 1, "summer": 2, "fall": 3}


def is_statement_timeout(error: DBAPIError) -> bool:
    """True if the database cancelled the query for passing STATEMENT_TIMEOUT_MS."""
    original = getattr(error, "orig", None)
    sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    if sqlstate is None:
        cause = getattr(original, "__cause__", None)
        sqlstate = getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)
    return sqlstate == STATEMENT_TIMEOUT_SQLSTATE


@contextmanager
def statement_timeout_to_503(endpoint: str):
    """Turn a database statement timeout into a 503, logged once here and nowhere else.

    Any other database error passes through unchanged.
    """
    try:
        yield
    except DBAPIError as error:
        if not is_statement_timeout(error):
            raise
        logger.warning("statement_timeout", endpoint=endpoint, limit_ms=STATEMENT_TIMEOUT_MS)
        raise ServiceUnavailableError() from error


def _escape_like(value: str) -> str:
    """Make `%`, `_` and the escape character literal, so search text cannot act as a pattern."""
    return (
        value.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", LIKE_ESCAPE + "%")
        .replace("_", LIKE_ESCAPE + "_")
    )


def _as_float(value) -> float | None:
    return None if value is None else float(value)


@dataclass(frozen=True)
class AnimeFilters:
    """Filters for the anime list. Values are the stored texts, already validated by the caller."""

    q: str | None = None
    # A list filter keeps an anime that matches ANY of its values; only tags can be set to need all.
    types: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ()
    seasons: tuple[str, ...] = ()
    year: int | None = None
    year_from: int | None = None
    year_to: int | None = None
    tag_ids: tuple[int, ...] = ()
    tag_match_all: bool = True
    studio_ids: tuple[int, ...] = ()
    release_status: ReleaseStatus | None = None
    # Keep anime that released an episode on or after this moment (the start of a UTC day).
    released_since: datetime | None = None
    min_score: float | None = None
    min_ratings: int | None = None


@dataclass(frozen=True)
class AggregateFilters:
    type: str | None = None
    year_from: int | None = None
    year_to: int | None = None


class CatalogueReadRepository:
    """Read-only list and aggregate queries over the catalogue, each under a 5 s timeout.

    Nothing here writes. Search text only ever reaches SQL as a bound parameter with `%` and `_`
    escaped, and every other filter is a typed value the controller validated.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def _limit_time(self) -> None:
        # SET LOCAL lasts for the current transaction only, which is this request's.
        await self.db.execute(text(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}"))

    # --- the anime list: search, filter, sort, paginate ----------------------------------

    @staticmethod
    def _word_match(q: str):
        """The start of the title, or of any word in it, in any of the three titles.

        One ILIKE on the combined text, so the trigram index `ix_anime_search_trgm` (built on
        exactly SEARCH_TEXT_SQL) can answer it. Each title is behind a space, so the start of a
        title is the start of a word, and the separator between titles keeps a search from
        matching across two of them.
        """
        return literal_column(SEARCH_TEXT_SQL, type_=Text).ilike(
            f"% {_escape_like(q)}%", escape=LIKE_ESCAPE
        )

    @staticmethod
    def _starts_with(q: str):
        """A title (in any language) that begins with the search text, for ranking."""
        prefix = f"{_escape_like(q)}%"
        titles = (Anime.title, Anime.title_english, Anime.title_japanese)
        return or_(*(t.ilike(prefix, escape=LIKE_ESCAPE) for t in titles))

    @staticmethod
    def _release_condition(release_status: ReleaseStatus):
        """Judge whether an anime is putting episodes out from the air dates of its episodes.

        An episode dated in the future is a schedule, not a release, so only `aired <= now`
        counts.
        """
        released = Episode.anime_id == Anime.mal_id, Episode.aired <= func.now()
        recent = exists().where(
            *released, Episode.aired >= func.now() - timedelta(days=RELEASING_WITHIN_DAYS)
        )
        if release_status is ReleaseStatus.RELEASING:
            return recent
        any_released = exists().where(*released)
        airing = Anime.status == STATUS_AIRING
        if release_status is ReleaseStatus.ON_BREAK:
            return and_(airing, any_released, ~recent)
        any_episode = exists().where(Episode.anime_id == Anime.mal_id)
        return and_(airing, ~any_episode)

    @staticmethod
    def _latest_episode_aired():
        """When the anime's most recent released episode aired (a correlated subquery)."""
        return (
            select(func.max(Episode.aired))
            .where(Episode.anime_id == Anime.mal_id, Episode.aired <= func.now())
            .correlate(Anime)
            .scalar_subquery()
        )

    def _anime_conditions(self, filters: AnimeFilters) -> list:
        conditions = []
        if filters.q:
            conditions.append(self._word_match(filters.q))
        if filters.types:
            conditions.append(Anime.type.in_(filters.types))
        if filters.statuses:
            conditions.append(Anime.status.in_(filters.statuses))
        if filters.seasons:
            conditions.append(Anime.season.in_(filters.seasons))
        if filters.year is not None:
            conditions.append(Anime.year == filters.year)
        if filters.year_from is not None:
            conditions.append(Anime.year >= filters.year_from)
        if filters.year_to is not None:
            conditions.append(Anime.year <= filters.year_to)
        if filters.tag_ids:
            with_tags = select(AnimeTag.anime_id).where(AnimeTag.tag_id.in_(filters.tag_ids))
            if filters.tag_match_all:
                # one row per matching tag, so having every wanted tag means a count of them all
                with_tags = with_tags.group_by(AnimeTag.anime_id).having(
                    func.count() == len(set(filters.tag_ids))
                )
            conditions.append(Anime.mal_id.in_(with_tags))
        if filters.studio_ids:
            conditions.append(
                Anime.mal_id.in_(
                    select(AnimeStudio.anime_id).where(
                        AnimeStudio.studio_id.in_(filters.studio_ids)
                    )
                )
            )
        if filters.release_status is not None:
            conditions.append(self._release_condition(filters.release_status))
        if filters.released_since is not None:
            conditions.append(
                exists().where(
                    Episode.anime_id == Anime.mal_id,
                    Episode.aired <= func.now(),
                    Episode.aired >= filters.released_since,
                )
            )
        if filters.min_score is not None:
            conditions.append(Anime.mal_score >= filters.min_score)
        if filters.min_ratings is not None:
            conditions.append(Anime.scored_by_mal >= filters.min_ratings)
        return conditions

    def _anime_order(self, sort: AnimeSort, order: Order | None, q: str | None) -> list:
        """The ORDER BY. Missing values always sort last, and `mal_id` makes pages stable."""
        if sort is AnimeSort.RELEVANCE:
            exact = func.lower(Anime.title) == q.lower()
            relevance = case((exact, 0), (self._starts_with(q), 1), else_=2)
            return [relevance.asc(), Anime.members_mal.desc().nulls_last(), Anime.mal_id.asc()]
        column, descending_by_default = {
            AnimeSort.MEMBERS: (Anime.members_mal, True),
            AnimeSort.SCORE: (Anime.mal_score, True),
            AnimeSort.FAVORITES: (Anime.favorites_mal, True),
            AnimeSort.NEWEST: (Anime.aired_from, True),
            AnimeSort.LATEST_EPISODE: (self._latest_episode_aired(), True),
            AnimeSort.TITLE: (func.lower(Anime.title), False),
        }[sort]
        descending = descending_by_default if order is None else order is Order.DESC
        return [(column.desc() if descending else column.asc()).nulls_last(), Anime.mal_id.asc()]

    async def search_anime(
        self,
        filters: AnimeFilters,
        sort: AnimeSort,
        order: Order | None,
        limit: int,
        offset: int,
    ) -> tuple[list, int]:
        """One page of anime matching the filters, and how many match in all.

        Only the columns a list shows are read (not the long synopsis and background text).
        """
        await self._limit_time()
        conditions = self._anime_conditions(filters)
        total = (
            await self.db.execute(select(func.count()).select_from(Anime).where(*conditions))
        ).scalar_one()
        rows = (
            await self.db.execute(
                select(
                    Anime.mal_id,
                    Anime.title,
                    Anime.title_english,
                    Anime.title_japanese,
                    Anime.type,
                    Anime.status,
                    Anime.season,
                    Anime.year,
                    Anime.episodes,
                    Anime.aired_from,
                    Anime.mal_score,
                    Anime.scored_by_mal,
                    Anime.members_mal,
                    Anime.favorites_mal,
                    Anime.rank_mal,
                    Anime.popularity_mal,
                    Anime.image_url,
                )
                .where(*conditions)
                .order_by(*self._anime_order(sort, order, filters.q))
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return list(rows), total

    async def last_episode_dates(self, anime_ids: list[int]) -> dict[int, datetime]:
        """When each of these anime last released an episode (only those that have released one)."""
        if not anime_ids:
            return {}
        rows = await self.db.execute(
            select(Episode.anime_id, func.max(Episode.aired))
            .where(Episode.anime_id.in_(anime_ids), Episode.aired <= func.now())
            .group_by(Episode.anime_id)
        )
        return {anime_id: aired for anime_id, aired in rows.all() if aired is not None}

    # --- options for the frontend's filter controls --------------------------------------

    async def list_tags(self) -> list[dict]:
        """Every tag with how many anime carry it, by name."""
        await self._limit_time()
        rows = await self.db.execute(
            select(Tag.mal_id, Tag.name, func.count(AnimeTag.anime_id))
            .outerjoin(AnimeTag, AnimeTag.tag_id == Tag.mal_id)
            .group_by(Tag.mal_id, Tag.name)
            .order_by(Tag.name)
        )
        return [
            {"tag_id": tag_id, "name": name, "anime_count": count} for tag_id, name, count in rows
        ]

    async def year_range(self) -> tuple[int | None, int | None]:
        await self._limit_time()
        row = (await self.db.execute(select(func.min(Anime.year), func.max(Anime.year)))).one()
        return row[0], row[1]

    async def list_studios(
        self,
        q: str | None,
        min_anime: int,
        sort: StudioSort,
        order: Order | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict], int]:
        """Studios whose name has a word starting with `q`, with how many anime each has.

        With a search text, names that start with it come first, then the chosen sort.
        """
        await self._limit_time()
        anime_count = func.count(AnimeStudio.anime_id)
        grouped = (
            select(Studio.mal_id.label("studio_id"), Studio.name, anime_count.label("anime_count"))
            .outerjoin(AnimeStudio, AnimeStudio.studio_id == Studio.mal_id)
            .group_by(Studio.mal_id, Studio.name)
            .having(anime_count >= min_anime)
        )
        ordering = []
        if q:
            escaped = _escape_like(q)
            grouped = grouped.where(
                or_(
                    Studio.name.ilike(f"{escaped}%", escape=LIKE_ESCAPE),
                    Studio.name.ilike(f"% {escaped}%", escape=LIKE_ESCAPE),
                )
            )
            starts = Studio.name.ilike(f"{escaped}%", escape=LIKE_ESCAPE)
            ordering.append(case((starts, 0), else_=1))
        by_count = sort is StudioSort.ANIME_COUNT
        descending = by_count if order is None else order is Order.DESC
        key = anime_count if by_count else func.lower(Studio.name)
        ordering += [key.desc() if descending else key.asc(), Studio.name, Studio.mal_id]

        total = (
            await self.db.execute(select(func.count()).select_from(grouped.subquery()))
        ).scalar_one()
        rows = await self.db.execute(grouped.order_by(*ordering).limit(limit).offset(offset))
        return (
            [
                {"studio_id": row.studio_id, "name": row.name, "anime_count": row.anime_count}
                for row in rows
            ],
            total,
        )

    # --- aggregates: one row per year, type, season, tag or studio -----------------------

    @staticmethod
    def _aggregate_conditions(filters: AggregateFilters) -> list:
        conditions = []
        if filters.type is not None:
            conditions.append(Anime.type == filters.type)
        if filters.year_from is not None:
            conditions.append(Anime.year >= filters.year_from)
        if filters.year_to is not None:
            conditions.append(Anime.year <= filters.year_to)
        return conditions

    @staticmethod
    def _group_stats(min_ratings: int):
        """The numbers every group row has, and the filter that picks the anime with a usable score.

        `anime_count` and `total_members` cover every anime in the group. The score numbers cover
        only anime that have a score and at least `min_ratings` ratings, and `scored_count` says
        how many those are.
        """
        scored = and_(Anime.mal_score.is_not(None), Anime.scored_by_mal >= min_ratings)
        stats = {
            "anime_count": func.count(),
            "scored_count": func.count().filter(scored),
            "mean_score": func.avg(Anime.mal_score).filter(scored),
            "median_score": func.percentile_cont(0.5).within_group(Anime.mal_score).filter(scored),
            "total_members": func.coalesce(func.sum(Anime.members_mal), 0),
        }
        return stats, scored

    @staticmethod
    def _stats_columns(stats: dict) -> list:
        return [expression.label(name) for name, expression in stats.items()]

    @staticmethod
    def _group_order(stats: dict, sort: GroupSort, order: Order):
        expression = stats[sort.value]
        return (expression.asc() if order is Order.ASC else expression.desc()).nulls_last()

    @staticmethod
    def _stat_values(row) -> dict:
        return {
            "anime_count": row.anime_count,
            "scored_count": row.scored_count,
            "mean_score": _as_float(row.mean_score),
            "median_score": _as_float(row.median_score),
            "total_members": int(row.total_members),
        }

    async def stats_by_year(self, filters: AggregateFilters, min_ratings: int) -> list[dict]:
        await self._limit_time()
        stats, _ = self._group_stats(min_ratings)
        rows = await self.db.execute(
            select(Anime.year, *self._stats_columns(stats))
            .where(Anime.year.is_not(None), *self._aggregate_conditions(filters))
            .group_by(Anime.year)
            .order_by(Anime.year)
        )
        return [{"year": row.year, **self._stat_values(row)} for row in rows.all()]

    async def stats_by_type(self, filters: AggregateFilters, min_ratings: int) -> list[dict]:
        await self._limit_time()
        stats, _ = self._group_stats(min_ratings)
        rows = await self.db.execute(
            select(Anime.type, *self._stats_columns(stats))
            .where(Anime.type.is_not(None), *self._aggregate_conditions(filters))
            .group_by(Anime.type)
            .order_by(stats["anime_count"].desc(), Anime.type)
        )
        return [{"type": row.type, **self._stat_values(row)} for row in rows.all()]

    async def stats_by_season(self, filters: AggregateFilters, min_ratings: int) -> list[dict]:
        await self._limit_time()
        stats, _ = self._group_stats(min_ratings)
        rows = await self.db.execute(
            select(Anime.season, *self._stats_columns(stats))
            .where(Anime.season.is_not(None), *self._aggregate_conditions(filters))
            .group_by(Anime.season)
            .order_by(case(SEASON_ORDER, value=Anime.season, else_=4))
        )
        return [{"season": row.season, **self._stat_values(row)} for row in rows.all()]

    async def stats_by_tag(
        self, filters: AggregateFilters, min_ratings: int, sort: GroupSort, order: Order
    ) -> list[dict]:
        await self._limit_time()
        stats, _ = self._group_stats(min_ratings)
        rows = await self.db.execute(
            select(Tag.mal_id.label("tag_id"), Tag.name, *self._stats_columns(stats))
            .select_from(Anime)
            .join(AnimeTag, AnimeTag.anime_id == Anime.mal_id)
            .join(Tag, Tag.mal_id == AnimeTag.tag_id)
            .where(*self._aggregate_conditions(filters))
            .group_by(Tag.mal_id, Tag.name)
            .order_by(self._group_order(stats, sort, order), Tag.name)
        )
        return [
            {"tag_id": row.tag_id, "name": row.name, **self._stat_values(row)} for row in rows.all()
        ]

    async def stats_by_studio(
        self,
        filters: AggregateFilters,
        min_ratings: int,
        min_anime: int,
        sort: GroupSort,
        order: Order,
        limit: int,
        offset: int,
    ) -> tuple[list[dict], int]:
        """Studios with at least `min_anime` anime that have a usable score, and how many there are."""
        await self._limit_time()
        stats, scored = self._group_stats(min_ratings)
        grouped = (
            select(Studio.mal_id.label("studio_id"), Studio.name, *self._stats_columns(stats))
            .select_from(Anime)
            .join(AnimeStudio, AnimeStudio.anime_id == Anime.mal_id)
            .join(Studio, Studio.mal_id == AnimeStudio.studio_id)
            .where(*self._aggregate_conditions(filters))
            .group_by(Studio.mal_id, Studio.name)
            .having(func.count().filter(scored) >= min_anime)
        )
        total = (
            await self.db.execute(select(func.count()).select_from(grouped.subquery()))
        ).scalar_one()
        rows = await self.db.execute(
            grouped.order_by(self._group_order(stats, sort, order), Studio.name)
            .limit(limit)
            .offset(offset)
        )
        return (
            [
                {"studio_id": row.studio_id, "name": row.name, **self._stat_values(row)}
                for row in rows.all()
            ],
            total,
        )
