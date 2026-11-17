from fastapi import Depends

from app.catalogue.repository import CatalogueRepository
from app.core.deps import create_catalogue_repository
from app.statistics.service import StatisticsService


def create_statistics_service(
    catalogue_repository: CatalogueRepository = Depends(create_catalogue_repository),
) -> StatisticsService:
    return StatisticsService(catalogue_repository)
