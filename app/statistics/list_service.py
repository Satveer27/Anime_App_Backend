import structlog

from app.catalogue.enums import API_TYPE, STORED_TYPE, AnimeType
from app.catalogue.read_repository import (
    AggregateFilters,
    CatalogueReadRepository,
    statement_timeout_to_503,
)
from app.exceptions import BadRequestError
from app.statistics.list_schemas import (
    HiddenGemRowSchema,
    HiddenGemsResponse,
    MostDroppedResponse,
    MostDroppedRowSchema,
)

logger = structlog.get_logger()

# Hidden gems: a score in the top 20% of the anime's type and members in the bottom 50%.
HIDDEN_GEM_SCORE_PERCENTILE = 0.80
HIDDEN_GEM_MEMBERS_PERCENTILE = 0.50
# The percentiles are ranked among anime with at least this many ratings (the same bar as the
# aggregates' MIN_RATINGS), so the long tail of barely rated anime does not decide "top 20%".
HIDDEN_GEM_COHORT_MIN_RATINGS = 50
# A gem needs at least this many ratings, so its score is not a handful of fans.
HIDDEN_GEM_MIN_SCORED_BY = 2_000
# Most dropped: tiny anime have noisy drop rates, so they need at least this many members.
DROPPED_MIN_MEMBERS = 10_000
PERCENTILE_DIGITS = 3
RATE_DIGITS = 4


def _api_type(stored: str) -> str:
    # A type MAL adds later is still returned, under a lowercased name, not dropped.
    return API_TYPE.get(stored, stored.lower().replace(" ", "_"))


class ListService:
    def __init__(self, read_repository: CatalogueReadRepository):
        self.read_repository = read_repository

    @staticmethod
    def _filters(type: AnimeType | None, year_from: int | None, year_to: int | None):
        if year_from is not None and year_to is not None and year_from > year_to:
            raise BadRequestError("year_from must not be after year_to.")
        return AggregateFilters(
            type=STORED_TYPE[type] if type else None, year_from=year_from, year_to=year_to
        )

    async def hidden_gems(
        self,
        type: AnimeType | None,
        year_from: int | None,
        year_to: int | None,
        limit: int,
        offset: int,
        min_ratings: int = HIDDEN_GEM_COHORT_MIN_RATINGS,
    ) -> HiddenGemsResponse:
        filters = self._filters(type, year_from, year_to)
        with statement_timeout_to_503("statistics_hidden_gems"):
            rows, total = await self.read_repository.hidden_gems(
                filters,
                min_ratings,
                HIDDEN_GEM_SCORE_PERCENTILE,
                HIDDEN_GEM_MEMBERS_PERCENTILE,
                HIDDEN_GEM_MIN_SCORED_BY,
                limit,
                offset,
            )
        logger.info("list_served", endpoint="hidden_gems", rows=len(rows), total=total)
        return HiddenGemsResponse(
            min_ratings=min_ratings,
            score_percentile=HIDDEN_GEM_SCORE_PERCENTILE,
            members_percentile=HIDDEN_GEM_MEMBERS_PERCENTILE,
            min_scored_by=HIDDEN_GEM_MIN_SCORED_BY,
            total=total,
            limit=limit,
            offset=offset,
            items=[
                HiddenGemRowSchema(
                    **{
                        **row,
                        "type": _api_type(row["type"]),
                        "score_percentile": round(row["score_percentile"], PERCENTILE_DIGITS),
                        "members_percentile": round(row["members_percentile"], PERCENTILE_DIGITS),
                    }
                )
                for row in rows
            ],
        )

    async def most_dropped(
        self,
        type: AnimeType | None,
        year_from: int | None,
        year_to: int | None,
        limit: int,
        offset: int,
        min_members: int = DROPPED_MIN_MEMBERS,
    ) -> MostDroppedResponse:
        filters = self._filters(type, year_from, year_to)
        with statement_timeout_to_503("statistics_most_dropped"):
            rows, total = await self.read_repository.most_dropped(
                filters, min_members, limit, offset
            )
        logger.info("list_served", endpoint="most_dropped", rows=len(rows), total=total)
        return MostDroppedResponse(
            min_members=min_members,
            total=total,
            limit=limit,
            offset=offset,
            items=[
                MostDroppedRowSchema(
                    **{
                        **row,
                        "type": _api_type(row["type"]),
                        "drop_rate": round(row["drop_rate"], RATE_DIGITS),
                    }
                )
                for row in rows
            ],
        )
