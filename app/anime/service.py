from datetime import UTC, date, datetime, time, timedelta

import structlog

from app.anime.schemas import AnimeListItemSchema, AnimeListResponse
from app.catalogue.enums import (
    STORED_SEASON,
    STORED_STATUS,
    STORED_TYPE,
    AnimeSeason,
    AnimeSort,
    AnimeStatus,
    AnimeType,
    Order,
    ReleaseStatus,
    TagMatch,
)
from app.catalogue.read_repository import (
    AnimeFilters,
    CatalogueReadRepository,
    statement_timeout_to_503,
)
from app.exceptions import BadRequestError

logger = structlog.get_logger()


def _utc_today() -> date:
    return datetime.now(UTC).date()


def released_since(days: int | None, today: date) -> datetime | None:
    """The start of the window for "released in the last `days` days", or None for no filter.

    Counted in calendar days, today included: 7 days is today and the six before it, so on a
    Sunday it is Monday to Sunday, and the episode that aired the Sunday before belongs to the
    previous week. Episode air dates are whole days at midnight UTC, so a rolling 7 x 24 hours
    would drop that episode in the afternoon but keep it in the morning.
    """
    if days is None:
        return None
    return datetime.combine(today - timedelta(days=days - 1), time.min, tzinfo=UTC)


class AnimeListService:
    def __init__(self, read_repository: CatalogueReadRepository):
        self.read_repository = read_repository

    async def list_anime(
        self,
        *,
        q: str | None,
        type: list[AnimeType] | None,
        status: list[AnimeStatus] | None,
        season: list[AnimeSeason] | None,
        year: int | None,
        year_from: int | None,
        year_to: int | None,
        tag_id: list[int] | None,
        tag_match: TagMatch,
        studio_id: list[int] | None,
        release_status: ReleaseStatus | None,
        released_days: int | None,
        min_score: float | None,
        min_ratings: int | None,
        sort: AnimeSort | None,
        order: Order | None,
        limit: int,
        offset: int,
    ) -> AnimeListResponse:
        """Search, filter, sort and paginate the catalogue.

        A filter that takes several values (type, status, season, studio) keeps an anime that
        matches any of them; tags need all of them unless `tag_match` is any. With a search text
        the default sort is relevance (exact title, then a title or word starting with the
        text, then the rest, each by members); without one it is members.
        """
        q = " ".join(q.split()) if q else None
        q = q or None
        if year is not None and (year_from is not None or year_to is not None):
            raise BadRequestError("Use either year, or year_from and year_to, not both.")
        if year_from is not None and year_to is not None and year_from > year_to:
            raise BadRequestError("year_from must not be after year_to.")
        if sort is AnimeSort.RELEVANCE and q is None:
            raise BadRequestError("Sorting by relevance needs a search text (q).")
        if sort is None:
            sort = AnimeSort.RELEVANCE if q else AnimeSort.MEMBERS

        filters = AnimeFilters(
            q=q,
            types=tuple(sorted({STORED_TYPE[t] for t in type or ()})),
            statuses=tuple(sorted({STORED_STATUS[s] for s in status or ()})),
            seasons=tuple(sorted({STORED_SEASON[s] for s in season or ()})),
            year=year,
            year_from=year_from,
            year_to=year_to,
            tag_ids=tuple(sorted(set(tag_id or ()))),
            tag_match_all=tag_match is TagMatch.ALL,
            studio_ids=tuple(sorted(set(studio_id or ()))),
            release_status=release_status,
            released_since=released_since(released_days, _utc_today()),
            min_score=min_score,
            min_ratings=min_ratings,
        )
        with statement_timeout_to_503("anime_list"):
            rows, total = await self.read_repository.search_anime(
                filters, sort, order, limit, offset
            )
            last_aired = await self.read_repository.last_episode_dates([row.mal_id for row in rows])

        # The search text is what a person typed, so it is never logged, only that there was one.
        logger.info(
            "anime_list_served",
            has_search=q is not None,
            sort=sort.value,
            release_status=release_status.value if release_status else None,
            returned=len(rows),
            total=total,
            limit=limit,
            offset=offset,
        )
        return AnimeListResponse(
            items=[
                AnimeListItemSchema.model_validate(row).model_copy(
                    update={"last_episode_aired": last_aired.get(row.mal_id)}
                )
                for row in rows
            ],
            total=total,
            limit=limit,
            offset=offset,
        )
