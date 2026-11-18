from fastapi import Depends

from app.catalogue.read_repository import CatalogueReadRepository
from app.catalogue.repository import CatalogueRepository
from app.core.cache import ResponseCache, get_response_cache
from app.core.deps import create_catalogue_read_repository, create_catalogue_repository
from app.statistics.aggregate_service import AggregateService
from app.statistics.list_service import ListService
from app.statistics.service import StatisticsService


def create_list_service(
    read_repository: CatalogueReadRepository = Depends(create_catalogue_read_repository),
    cache: ResponseCache = Depends(get_response_cache),
) -> ListService:
    return ListService(read_repository, cache)


def create_aggregate_service(
    read_repository: CatalogueReadRepository = Depends(create_catalogue_read_repository),
    cache: ResponseCache = Depends(get_response_cache),
) -> AggregateService:
    return AggregateService(read_repository, cache)


def create_statistics_service(
    catalogue_repository: CatalogueRepository = Depends(create_catalogue_repository),
    cache: ResponseCache = Depends(get_response_cache),
) -> StatisticsService:
    return StatisticsService(catalogue_repository, cache)
