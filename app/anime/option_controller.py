from fastapi import APIRouter, Depends, Query, Response

from app.anime.deps import create_options_service
from app.anime.option_schemas import FilterOptionsResponse, StudioListResponse
from app.anime.option_service import OptionsService
from app.catalogue.enums import Order, StudioSort

options_router = APIRouter(tags=["options"])

# The same for everyone and it changes only when the catalogue refreshes, so browsers and
# nginx may keep it for an hour.
FILTERS_CACHE_CONTROL = "public, max-age=3600"


# Public and read-only: no auth, rate limiting is nginx's job (see the epic, #49).
@options_router.get(
    "/filters",
    response_model=FilterOptionsResponse,
    summary="Everything the anime list's filter controls offer",
    description=(
        "The fixed choices (types, statuses, seasons, release statuses, sorts, orders, tag "
        "matching), every tag with its anime count, the range of years and the paging limits, "
        "in one call. Studios are too many for a dropdown: use `/studios`. Genres, themes and "
        "demographics share one tag list because MAL does not say which is which."
    ),
)
async def get_filter_options(
    response: Response, service: OptionsService = Depends(create_options_service)
):
    response.headers["Cache-Control"] = FILTERS_CACHE_CONTROL
    return await service.filter_options()


@options_router.get(
    "/studios",
    response_model=StudioListResponse,
    summary="Search and list studios for the studio filter",
    description=(
        "Studios with how many anime each has. `q` matches the start of the name or of any word "
        "in it, names that start with it first. Sorted by number of anime (default) or name, "
        "paginated. Send the chosen `studio_id` to the anime list."
    ),
)
async def list_studios(
    q: str | None = Query(None, min_length=1, max_length=100, description="Studio name search"),
    min_anime: int = Query(1, ge=1, le=10_000, description="Hide studios with fewer anime"),
    sort: StudioSort = Query(StudioSort.ANIME_COUNT),
    order: Order | None = Query(None, description="Default: desc by count, asc by name"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10_000),
    service: OptionsService = Depends(create_options_service),
):
    return await service.list_studios(q, min_anime, sort, order, limit, offset)
