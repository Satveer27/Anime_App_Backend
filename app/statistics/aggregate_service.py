import structlog

from app.catalogue.enums import API_TYPE, STORED_TYPE, AnimeType, GroupSort, Order
from app.catalogue.read_repository import (
    AggregateFilters,
    CatalogueReadRepository,
    statement_timeout_to_503,
)
from app.exceptions import BadRequestError
from app.statistics.aggregate_schemas import (
    BySeasonResponse,
    ByStudioResponse,
    ByTagResponse,
    ByTypeResponse,
    ByYearResponse,
    SeasonRowSchema,
    StudioRowSchema,
    TagRowSchema,
    TypeRowSchema,
    YearRowSchema,
)
from app.statistics.service import LOW_SAMPLE_VOTES

logger = structlog.get_logger()

# An anime needs at least this many MAL ratings to count in a group's score numbers: the same
# bar as the per-anime `low_sample` flag, so a score is never trusted in one place and not in
# the other.
MIN_RATINGS = LOW_SAMPLE_VOTES
# Digits kept in the scores of a group row.
SCORE_DIGITS = 3


class AggregateService:
    def __init__(self, read_repository: CatalogueReadRepository):
        self.read_repository = read_repository

    @staticmethod
    def _filters(type: AnimeType | None, year_from: int | None, year_to: int | None):
        if year_from is not None and year_to is not None and year_from > year_to:
            raise BadRequestError("year_from must not be after year_to.")
        return AggregateFilters(
            type=STORED_TYPE[type] if type else None, year_from=year_from, year_to=year_to
        )

    @staticmethod
    def _round(row: dict) -> dict:
        """Round the scores so a response shows 6.787, not 6.787462568951931."""
        for key in ("mean_score", "median_score"):
            if row[key] is not None:
                row[key] = round(row[key], SCORE_DIGITS)
        return row

    def _served(self, endpoint: str, count: int, **fields) -> None:
        logger.info("aggregate_served", endpoint=endpoint, rows=count, **fields)

    async def by_year(
        self, type: AnimeType | None, year_from: int | None, year_to: int | None
    ) -> ByYearResponse:
        filters = self._filters(type, year_from, year_to)
        with statement_timeout_to_503("statistics_by_year"):
            rows = await self.read_repository.stats_by_year(filters, MIN_RATINGS)
        self._served("by_year", len(rows))
        return ByYearResponse(
            min_ratings=MIN_RATINGS,
            items=[YearRowSchema(**self._round(row)) for row in rows],
        )

    async def by_type(self, year_from: int | None, year_to: int | None) -> ByTypeResponse:
        filters = self._filters(None, year_from, year_to)
        with statement_timeout_to_503("statistics_by_type"):
            rows = await self.read_repository.stats_by_type(filters, MIN_RATINGS)
        self._served("by_type", len(rows))
        items = []
        for row in rows:
            stored = row.pop("type")
            # A type MAL adds later is still returned, under a lowercased name, not dropped.
            items.append(
                TypeRowSchema(
                    type=API_TYPE.get(stored, stored.lower().replace(" ", "_")),
                    label=stored,
                    **self._round(row),
                )
            )
        return ByTypeResponse(min_ratings=MIN_RATINGS, items=items)

    async def by_season(
        self, type: AnimeType | None, year_from: int | None, year_to: int | None
    ) -> BySeasonResponse:
        filters = self._filters(type, year_from, year_to)
        with statement_timeout_to_503("statistics_by_season"):
            rows = await self.read_repository.stats_by_season(filters, MIN_RATINGS)
        self._served("by_season", len(rows))
        return BySeasonResponse(
            min_ratings=MIN_RATINGS,
            items=[SeasonRowSchema(**self._round(row)) for row in rows],
        )

    async def by_tag(
        self,
        type: AnimeType | None,
        year_from: int | None,
        year_to: int | None,
        sort: GroupSort,
        order: Order,
    ) -> ByTagResponse:
        filters = self._filters(type, year_from, year_to)
        with statement_timeout_to_503("statistics_by_tag"):
            rows = await self.read_repository.stats_by_tag(filters, MIN_RATINGS, sort, order)
        self._served("by_tag", len(rows), sort=sort.value)
        return ByTagResponse(
            min_ratings=MIN_RATINGS,
            items=[TagRowSchema(**self._round(row)) for row in rows],
        )

    async def by_studio(
        self,
        type: AnimeType | None,
        year_from: int | None,
        year_to: int | None,
        min_anime: int,
        sort: GroupSort,
        order: Order,
        limit: int,
        offset: int,
    ) -> ByStudioResponse:
        filters = self._filters(type, year_from, year_to)
        with statement_timeout_to_503("statistics_by_studio"):
            rows, total = await self.read_repository.stats_by_studio(
                filters, MIN_RATINGS, min_anime, sort, order, limit, offset
            )
        self._served("by_studio", len(rows), total=total, sort=sort.value)
        return ByStudioResponse(
            min_ratings=MIN_RATINGS,
            min_anime=min_anime,
            total=total,
            limit=limit,
            offset=offset,
            items=[StudioRowSchema(**self._round(row)) for row in rows],
        )
