from fastapi import APIRouter, Depends, Path

from app.statistics.deps import create_statistics_service
from app.statistics.schemas import AnimeStatisticsResponse, EpisodeScoresResponse
from app.statistics.service import StatisticsService

anime_statistics_router = APIRouter(prefix="/anime", tags=["statistics"])


# Public and read-only: no auth, rate limiting is nginx's job (see the epic, #49).
@anime_statistics_router.get(
    "/{mal_id}/statistics",
    response_model=AnimeStatisticsResponse,
    summary="Statistics for one anime",
    responses={404: {"description": "No anime with this MAL id"}},
)
async def get_anime_statistics(
    mal_id: int = Path(..., ge=1, description="The anime's MAL id"),
    service: StatisticsService = Depends(create_statistics_service),
):
    return await service.get_anime_statistics(mal_id)


@anime_statistics_router.get(
    "/{mal_id}/statistics/episodes",
    response_model=EpisodeScoresResponse,
    summary="Episode scores and the per-season 'gets good' trend",
    responses={404: {"description": "No anime with this MAL id"}},
)
async def get_episode_scores(
    mal_id: int = Path(..., ge=1, description="The anime's MAL id"),
    service: StatisticsService = Depends(create_statistics_service),
):
    return await service.get_episode_scores(mal_id)
