from datetime import UTC, date, datetime

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
    TagMatch,
)
from app.catalogue.read_repository import (
    AnimeFilters,
    CatalogueReadRepository,
    statement_timeout_to_503,
)
from app.core.cache import ResponseCache, cached
from app.exceptions import BadRequestError

logger = structlog.get_logger()


# Page sizes the frontend may ask for. Any other size is not cached.
CACHED_PAGE_SIZES = (20, 50, 100)
# The plain list is cached for its first pages only: deeper pages are rarely read, and the
# number of different offsets is large.
UNFILTERED_CACHED_PAGES = 10
# The presets (airing, upcoming, the current season) have a few hundred anime each (271, 233 and
# 115 on 18 Nov 2026), so every page is cached, up to this offset. The cap keeps a client that
# asks for offsets far past the end from creating a key for each.
PRESET_MAX_OFFSET = 1000
PRESET_STATUSES = (AnimeStatus.AIRING, AnimeStatus.UPCOMING)
# These filters must be unset for a request to be cached; status, season, year and year_from have
# their own rules below.
_UNSET_FOR_CACHE = ("q", "type", "tag_id", "studio_id", "min_score", "min_ratings", "year_to")


def current_season(today: date | None = None) -> tuple[AnimeSeason, int]:
    """The season and year of today's date, with MAL's calendar (winter is January to March)."""
    today = today or datetime.now(UTC).date()
    seasons = (AnimeSeason.WINTER, AnimeSeason.SPRING, AnimeSeason.SUMMER, AnimeSeason.FALL)
    return seasons[(today.month - 1) // 3], today.year


def _cacheable_list(args: dict) -> bool:
    """The plain list's first pages and every page of the presets, in any sort order.

    The presets are:
    - a single `status` of airing or upcoming, alone;
    - the same status with `year` of this year (airing anime that started this year, which is
      the main page's "top airing": a show that premiered in winter and is still airing is not
      in this season);
    - the same status with `year_from` of last year (also the shows that started late last year);
    - the current season and year, with no status.

    The page size must be one of CACHED_PAGE_SIZES and the offset a multiple of it, so the pages
    are the ones a frontend walks through, not arbitrary offsets.
    """
    limit, offset = args["limit"], args["offset"]
    if limit not in CACHED_PAGE_SIZES or offset % limit != 0:
        return False
    if args["tag_match"] is not TagMatch.ALL or any(args[n] is not None for n in _UNSET_FOR_CACHE):
        return False
    status, season = args["status"], args["season"]
    year, year_from = args["year"], args["year_from"]
    if status is None and season is None and year is None and year_from is None:
        return offset < UNFILTERED_CACHED_PAGES * limit
    if offset > PRESET_MAX_OFFSET:
        return False
    now_season, now_year = current_season()
    if season is None and status is not None:
        if len(status) != 1 or status[0] not in PRESET_STATUSES:
            return False
        if year is None:
            return year_from is None or year_from == now_year - 1
        return year == now_year and year_from is None
    if status is None and season is not None and year is not None and year_from is None:
        return list(season) == [now_season] and year == now_year
    return False


class AnimeListService:
    def __init__(
        self, read_repository: CatalogueReadRepository, cache: ResponseCache | None = None
    ):
        self.read_repository = read_repository
        self.cache = cache

    @cached("anime_list", AnimeListResponse, cacheable=_cacheable_list)
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
            min_score=min_score,
            min_ratings=min_ratings,
        )
        with statement_timeout_to_503("anime_list"):
            rows, total = await self.read_repository.search_anime(
                filters, sort, order, limit, offset
            )

        # The search text is what a person typed, so it is never logged, only that there was one.
        logger.info(
            "anime_list_served",
            has_search=q is not None,
            sort=sort.value,
            returned=len(rows),
            total=total,
            limit=limit,
            offset=offset,
        )
        return AnimeListResponse(
            items=[AnimeListItemSchema.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )
