import structlog

from app.anime.limits import MAX_FILTER_VALUES, MAX_PAGE_SIZE
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
    StudioSort,
    TagMatch,
)
from app.catalogue.read_repository import (
    CatalogueReadRepository,
    statement_timeout_to_503,
)
from app.core.cache import ResponseCache, cached

logger = structlog.get_logger()

SORT_LABELS = {
    AnimeSort.RELEVANCE: ("Best match", "Needs a search text (q); the default when there is one"),
    AnimeSort.MEMBERS: ("Most members", "The default without a search text"),
    AnimeSort.SCORE: ("Highest score", None),
    AnimeSort.FAVORITES: ("Most favorites", None),
    AnimeSort.NEWEST: ("Newest first", "By the date the anime started airing"),
    AnimeSort.TITLE: ("Title", "A to Z unless order=desc"),
}


def fixed_options() -> dict:
    """The choices that come from enums, not from the database."""
    return {
        "types": [OptionSchema(value=t.value, label=STORED_TYPE[t]) for t in AnimeType],
        "statuses": [OptionSchema(value=s.value, label=STORED_STATUS[s]) for s in AnimeStatus],
        "seasons": [OptionSchema(value=s.value, label=s.value.title()) for s in AnimeSeason],
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


def _default_studio_page(args: dict) -> bool:
    """Only the unsearched first page of the default studio list."""
    return (
        args["q"] is None and args["min_anime"] == 1 and args["limit"] == 20 and args["offset"] == 0
    )


class OptionsService:
    def __init__(
        self, read_repository: CatalogueReadRepository, cache: ResponseCache | None = None
    ):
        self.read_repository = read_repository
        self.cache = cache

    @cached("filter_options", FilterOptionsResponse)
    async def filter_options(self) -> FilterOptionsResponse:
        with statement_timeout_to_503("filter_options"):
            tags = await self.read_repository.list_tags()
            year_min, year_max = await self.read_repository.year_range()
        logger.info("filter_options_served", tags=len(tags))
        return FilterOptionsResponse(
            **fixed_options(),
            years=YearRangeSchema(min=year_min, max=year_max),
            limits=LimitsSchema(max_page_size=MAX_PAGE_SIZE, max_filter_values=MAX_FILTER_VALUES),
            tags=[TagOptionSchema(**tag) for tag in tags],
        )

    @cached("studio_list", StudioListResponse, cacheable=_default_studio_page)
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
