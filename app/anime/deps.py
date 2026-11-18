from fastapi import Depends

from app.anime.option_service import OptionsService
from app.anime.service import AnimeListService
from app.catalogue.read_repository import CatalogueReadRepository
from app.core.cache import ResponseCache, get_response_cache
from app.core.deps import create_catalogue_read_repository


def create_options_service(
    read_repository: CatalogueReadRepository = Depends(create_catalogue_read_repository),
    cache: ResponseCache = Depends(get_response_cache),
) -> OptionsService:
    return OptionsService(read_repository, cache)


def create_anime_list_service(
    read_repository: CatalogueReadRepository = Depends(create_catalogue_read_repository),
    cache: ResponseCache = Depends(get_response_cache),
) -> AnimeListService:
    return AnimeListService(read_repository, cache)
