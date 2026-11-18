from fastapi import APIRouter, Depends, Query

from app.catalogue.enums import AnimeType, GroupSort, Order
from app.statistics.aggregate_schemas import (
    BySeasonResponse,
    ByStudioResponse,
    ByTagResponse,
    ByTypeResponse,
    ByYearResponse,
)
from app.statistics.aggregate_service import MIN_RATINGS, AggregateService
from app.statistics.deps import create_aggregate_service

aggregate_router = APIRouter(prefix="/statistics", tags=["statistics"])

# Public and read-only: no auth, rate limiting is nginx's job (see the epic, #49).
_SCORES_NOTE = (
    f"Scores use only anime with a score and at least `min_ratings` ratings (default {MIN_RATINGS}); "
    "`scored_count` says how many that is."
)
MIN_RATINGS_QUERY = Query(
    MIN_RATINGS,
    ge=0,
    le=100_000,
    description="Anime need at least this many MAL ratings to count in the score numbers",
)
TYPE_FILTER = Query(None, description="Only anime of this type")
YEAR_FROM = Query(
    None, ge=1900, le=2100, description="Only anime that started in or after this year"
)
YEAR_TO = Query(
    None, ge=1900, le=2100, description="Only anime that started in or before this year"
)


@aggregate_router.get(
    "/by-year",
    response_model=ByYearResponse,
    summary="Anime count and average score for each year",
    description="One row per start year, oldest first. " + _SCORES_NOTE,
)
async def by_year(
    type: AnimeType | None = TYPE_FILTER,
    year_from: int | None = YEAR_FROM,
    year_to: int | None = YEAR_TO,
    min_ratings: int = MIN_RATINGS_QUERY,
    service: AggregateService = Depends(create_aggregate_service),
):
    return await service.by_year(type, year_from, year_to, min_ratings)


@aggregate_router.get(
    "/by-type",
    response_model=ByTypeResponse,
    summary="Anime count and average score for each type",
    description="One row per type (TV, Movie, ...), most anime first. " + _SCORES_NOTE,
)
async def by_type(
    year_from: int | None = YEAR_FROM,
    year_to: int | None = YEAR_TO,
    min_ratings: int = MIN_RATINGS_QUERY,
    service: AggregateService = Depends(create_aggregate_service),
):
    return await service.by_type(year_from, year_to, min_ratings)


@aggregate_router.get(
    "/by-season",
    response_model=BySeasonResponse,
    summary="Anime count and average score for winter, spring, summer and fall",
    description="Four rows, in calendar order, across the chosen years. " + _SCORES_NOTE,
)
async def by_season(
    type: AnimeType | None = TYPE_FILTER,
    year_from: int | None = YEAR_FROM,
    year_to: int | None = YEAR_TO,
    min_ratings: int = MIN_RATINGS_QUERY,
    service: AggregateService = Depends(create_aggregate_service),
):
    return await service.by_season(type, year_from, year_to, min_ratings)


@aggregate_router.get(
    "/by-tag",
    response_model=ByTagResponse,
    summary="Anime count and average score for each genre, theme and demographic",
    description=(
        "One row per tag. An anime has several tags, so it counts in each of them and the "
        "rows do not add up to the catalogue. Genres, themes and demographics share one "
        "list. " + _SCORES_NOTE
    ),
)
async def by_tag(
    type: AnimeType | None = TYPE_FILTER,
    year_from: int | None = YEAR_FROM,
    year_to: int | None = YEAR_TO,
    sort: GroupSort = Query(GroupSort.ANIME_COUNT),
    order: Order = Query(Order.DESC),
    min_ratings: int = MIN_RATINGS_QUERY,
    service: AggregateService = Depends(create_aggregate_service),
):
    return await service.by_tag(type, year_from, year_to, sort, order, min_ratings)


@aggregate_router.get(
    "/by-studio",
    response_model=ByStudioResponse,
    summary="Anime count and average score for each studio",
    description=(
        "A studio needs at least `min_anime` anime with a usable score to appear. Paginated. "
        + _SCORES_NOTE
    ),
)
async def by_studio(
    type: AnimeType | None = TYPE_FILTER,
    year_from: int | None = YEAR_FROM,
    year_to: int | None = YEAR_TO,
    min_anime: int = Query(10, ge=1, le=100),
    sort: GroupSort = Query(GroupSort.ANIME_COUNT),
    order: Order = Query(Order.DESC),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10_000),
    min_ratings: int = MIN_RATINGS_QUERY,
    service: AggregateService = Depends(create_aggregate_service),
):
    return await service.by_studio(
        type, year_from, year_to, min_anime, sort, order, limit, offset, min_ratings
    )
