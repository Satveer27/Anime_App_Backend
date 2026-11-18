import structlog

from app.anime.limits import MAX_FILTER_VALUES, MAX_PAGE_SIZE, MAX_RELEASED_DAYS
from app.anime.option_schemas import (
    FilterOptionsResponse,
    LimitsSchema,
    OptionSchema,
    StudioListResponse,
    StudioOptionSchema,
    TagOptionSchema,
    YearRangeSchema,
)
from app.catalogue.enums import (
    STORED_STATUS,
    STORED_TYPE,
    AnimeSeason,
    AnimeSort,
    AnimeStatus,
    AnimeType,
    Order,
    ReleaseStatus,
    StudioSort,
    TagMatch,
)
from app.catalogue.read_repository import (
    RELEASING_WITHIN_DAYS,
    CatalogueReadRepository,
    statement_timeout_to_503,
)

logger = structlog.get_logger()

SORT_LABELS = {
    AnimeSort.RELEVANCE: ("Best match", "Needs a search text (q); the default when there is one"),
    AnimeSort.MEMBERS: ("Most members", "The default without a search text"),
    AnimeSort.SCORE: ("Highest score", None),
    AnimeSort.FAVORITES: ("Most favorites", None),
    AnimeSort.NEWEST: ("Newest first", "By the date the anime started airing"),
    AnimeSort.LATEST_EPISODE: (
        "Latest episode",
        "By the date of the latest released episode; meant to be used with a filter such as "
        "status=airing",
    ),
    AnimeSort.TITLE: ("Title", "A to Z unless order=desc"),
}
RELEASE_LABELS = {
    ReleaseStatus.RELEASING: (
        "Releasing now",
        f"An episode aired in the last {RELEASING_WITHIN_DAYS} days",
    ),
    ReleaseStatus.ON_BREAK: (
        "On a break",
        f"Airing, but no episode in the last {RELEASING_WITHIN_DAYS} days",
    ),
    ReleaseStatus.NO_EPISODE_DATA: ("No episode data", "Airing, but we hold no episodes for it"),
}


def fixed_options() -> dict:
    """The choices that come from enums, not from the database."""
    return {
        "types": [OptionSchema(value=t.value, label=STORED_TYPE[t]) for t in AnimeType],
        "statuses": [OptionSchema(value=s.value, label=STORED_STATUS[s]) for s in AnimeStatus],
        "seasons": [OptionSchema(value=s.value, label=s.value.title()) for s in AnimeSeason],
        "release_statuses": [
            OptionSchema(
                value=r.value, label=RELEASE_LABELS[r][0], description=RELEASE_LABELS[r][1]
            )
            for r in ReleaseStatus
        ],
        "sorts": [
            OptionSchema(value=s.value, label=SORT_LABELS[s][0], description=SORT_LABELS[s][1])
            for s in AnimeSort
        ],
        "orders": [
            OptionSchema(value=Order.DESC.value, label="Descending"),
            OptionSchema(value=Order.ASC.value, label="Ascending"),
        ],
        "tag_matches": [
            OptionSchema(value=TagMatch.ALL.value, label="All of the tags"),
            OptionSchema(value=TagMatch.ANY.value, label="Any of the tags"),
        ],
    }


class OptionsService:
    def __init__(self, read_repository: CatalogueReadRepository):
        self.read_repository = read_repository

    async def filter_options(self) -> FilterOptionsResponse:
        with statement_timeout_to_503("filter_options"):
            tags = await self.read_repository.list_tags()
            year_min, year_max = await self.read_repository.year_range()
        logger.info("filter_options_served", tags=len(tags))
        return FilterOptionsResponse(
            **fixed_options(),
            years=YearRangeSchema(min=year_min, max=year_max),
            limits=LimitsSchema(
                max_page_size=MAX_PAGE_SIZE,
                max_filter_values=MAX_FILTER_VALUES,
                max_released_days=MAX_RELEASED_DAYS,
            ),
            tags=[TagOptionSchema(**tag) for tag in tags],
        )

    async def list_studios(
        self,
        q: str | None,
        min_anime: int,
        sort: StudioSort,
        order: Order | None,
        limit: int,
        offset: int,
    ) -> StudioListResponse:
        q = " ".join(q.split()) if q else None
        q = q or None
        with statement_timeout_to_503("studio_list"):
            rows, total = await self.read_repository.list_studios(
                q, min_anime, sort, order, limit, offset
            )
        # The search text is what a person typed, so it is never logged, only that there was one.
        logger.info("studio_list_served", has_search=q is not None, returned=len(rows), total=total)
        return StudioListResponse(
            items=[StudioOptionSchema(**row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )
