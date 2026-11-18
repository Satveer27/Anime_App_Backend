from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from app.anime.deps import create_anime_list_service
from app.anime.limits import MAX_FILTER_VALUES, MAX_PAGE_SIZE, MAX_RELEASED_DAYS
from app.anime.schemas import AnimeListResponse
from app.anime.service import AnimeListService
from app.catalogue.enums import (
    AnimeSeason,
    AnimeSort,
    AnimeStatus,
    AnimeType,
    Order,
    ReleaseStatus,
    TagMatch,
)
from app.catalogue.read_repository import RELEASING_WITHIN_DAYS

anime_router = APIRouter(prefix="/anime", tags=["anime"])

PositiveId = Annotated[int, Field(ge=1, le=10_000_000)]


# Public and read-only: no auth, rate limiting is nginx's job (see the epic, #49).
@anime_router.get(
    "",
    response_model=AnimeListResponse,
    summary="Search, filter and sort anime",
    description=(
        "Search by title (the start of the title or of any word in it, also in the English and "
        "Japanese titles) and filter by type, status, season, year, tag, studio, release status, "
        "score and number of ratings. All filters are optional and combine with AND. "
        "`type`, `status`, `season`, `tag_id` and `studio_id` can be repeated "
        f"(up to {MAX_FILTER_VALUES}, e.g. `status=airing&status=upcoming`): an anime matching "
        "any of the values is kept, except tags, which must all match unless `tag_match=any`. "
        "`release_status` looks at episode air dates: `releasing` = an episode aired in the last "
        f"{RELEASING_WITHIN_DAYS} days, `on_break` = airing with episodes but none in that window, "
        "`no_episode_data` = airing but we hold no episodes. "
        "`released_days=N` keeps the anime that released an episode in the last N calendar days, "
        "today included: `released_days=7` on a Sunday is Monday to Sunday. "
        "`sort`: members, score, favorites, newest, latest_episode, title, or relevance "
        "(needs `q`). Defaults to relevance with `q` and members without it; missing values sort "
        "last. For the top airing anime that are really releasing: "
        "`status=airing&release_status=releasing&sort=members`. For this week's releases: "
        "`released_days=7&sort=latest_episode`."
    ),
)
async def list_anime(
    q: str | None = Query(None, min_length=1, max_length=100, description="Title search text"),
    type: list[AnimeType] | None = Query(None, max_length=MAX_FILTER_VALUES),
    status: list[AnimeStatus] | None = Query(
        None, max_length=MAX_FILTER_VALUES, description="airing, finished or upcoming"
    ),
    season: list[AnimeSeason] | None = Query(None, max_length=MAX_FILTER_VALUES),
    year: int | None = Query(None, ge=1900, le=2100, description="One year"),
    year_from: int | None = Query(None, ge=1900, le=2100),
    year_to: int | None = Query(None, ge=1900, le=2100),
    tag_id: list[PositiveId] | None = Query(None, max_length=MAX_FILTER_VALUES),
    tag_match: TagMatch = Query(TagMatch.ALL, description="With several tags: all or any"),
    studio_id: list[PositiveId] | None = Query(None, max_length=MAX_FILTER_VALUES),
    release_status: ReleaseStatus | None = Query(None),
    released_days: int | None = Query(
        None,
        ge=1,
        le=MAX_RELEASED_DAYS,
        description="Released an episode in the last N calendar days, today included (7 = this "
        "week's releases when called on a Sunday)",
    ),
    min_score: float | None = Query(None, ge=0, le=10),
    min_ratings: int | None = Query(None, ge=0, description="Minimum number of MAL ratings"),
    sort: AnimeSort | None = Query(None),
    order: Order | None = Query(None, description="Default: desc, except asc for title"),
    limit: int = Query(20, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0, le=50_000),
    service: AnimeListService = Depends(create_anime_list_service),
):
    return await service.list_anime(
        q=q,
        type=type,
        status=status,
        season=season,
        year=year,
        year_from=year_from,
        year_to=year_to,
        tag_id=tag_id,
        tag_match=tag_match,
        studio_id=studio_id,
        release_status=release_status,
        released_days=released_days,
        min_score=min_score,
        min_ratings=min_ratings,
        sort=sort,
        order=order,
        limit=limit,
        offset=offset,
    )
