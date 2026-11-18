from fastapi import Depends

from app.anime.option_service import OptionsService
from app.anime.service import AnimeListService
from app.catalogue.read_repository import CatalogueReadRepository
from app.core.deps import create_catalogue_read_repository


def create_options_service(
    read_repository: CatalogueReadRepository = Depends(create_catalogue_read_repository),
) -> OptionsService:
    return OptionsService(read_repository)


def create_anime_list_service(
    read_repository: CatalogueReadRepository = Depends(create_catalogue_read_repository),
) -> AnimeListService:
    return AnimeListService(read_repository)
