from fastapi import APIRouter, Depends, Query

from app.catalogue.enums import AnimeType
from app.statistics.deps import create_list_service
from app.statistics.list_schemas import HiddenGemsResponse, MostDroppedResponse
from app.statistics.list_service import (
    DROPPED_MIN_MEMBERS,
    HIDDEN_GEM_COHORT_MIN_RATINGS,
    HIDDEN_GEM_MEMBERS_PERCENTILE,
    HIDDEN_GEM_MIN_SCORED_BY,
    HIDDEN_GEM_SCORE_PERCENTILE,
    ListService,
)

list_router = APIRouter(prefix="/statistics", tags=["statistics"])

# Public and read-only: no auth, rate limiting is nginx's job (see the epic, #49).
TYPE_FILTER = Query(None, description="Only anime of this type")
YEAR_FROM = Query(
    None, ge=1900, le=2100, description="Only anime that started in or after this year"
)
YEAR_TO = Query(
    None, ge=1900, le=2100, description="Only anime that started in or before this year"
)
LIMIT = Query(20, ge=1, le=100)
OFFSET = Query(0, ge=0, le=10_000)

_TOP_SHARE = round((1 - HIDDEN_GEM_SCORE_PERCENTILE) * 100)
_BOTTOM_SHARE = round(HIDDEN_GEM_MEMBERS_PERCENTILE * 100)


@list_router.get(
    "/hidden-gems",
    response_model=HiddenGemsResponse,
    summary="Highly rated anime that few people have watched",
    description=(
        f"An anime qualifies when its score is in the top {_TOP_SHARE}% of its type, its "
        f"members are in the bottom {_BOTTOM_SHARE}% of its type, and it has at least "
        f"{HIDDEN_GEM_MIN_SCORED_BY:,} MAL ratings. Percentiles are ranked among the anime of "
        f"that type with at least `min_ratings` ratings (default {HIDDEN_GEM_COHORT_MIN_RATINGS}), "
        "across the whole catalogue: the type and year filters only narrow the result. Best "
        "score first, fewer members first on a tie. Paginated."
    ),
)
async def hidden_gems(
    type: AnimeType | None = TYPE_FILTER,
    year_from: int | None = YEAR_FROM,
    year_to: int | None = YEAR_TO,
    limit: int = LIMIT,
    offset: int = OFFSET,
    min_ratings: int = Query(
        HIDDEN_GEM_COHORT_MIN_RATINGS,
        ge=0,
        le=100_000,
        description="Percentiles are ranked among anime of the type with at least this many ratings",
    ),
    service: ListService = Depends(create_list_service),
):
    return await service.hidden_gems(type, year_from, year_to, limit, offset, min_ratings)


@list_router.get(
    "/most-dropped",
    response_model=MostDroppedResponse,
    summary="Anime that the most people started and then dropped",
    description=(
        "Ranked by drop rate: dropped / (members - plan to watch), the same definition as the "
        "per-anime statistics. An anime needs at least `min_members` MAL members (default "
        f"{DROPPED_MIN_MEMBERS:,}), and anime that have not aired are left out. Paginated."
    ),
)
async def most_dropped(
    type: AnimeType | None = TYPE_FILTER,
    year_from: int | None = YEAR_FROM,
    year_to: int | None = YEAR_TO,
    limit: int = LIMIT,
    offset: int = OFFSET,
    min_members: int = Query(
        DROPPED_MIN_MEMBERS,
        ge=0,
        le=10_000_000,
        description="Anime need at least this many MAL members",
    ),
    service: ListService = Depends(create_list_service),
):
    return await service.most_dropped(type, year_from, year_to, limit, offset, min_members)
