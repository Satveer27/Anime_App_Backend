from fastapi import APIRouter

from app.anime.controller import anime_router
from app.anime.option_controller import options_router
from app.core.health import router as health_router
from app.security.controller import auth_router
from app.statistics.aggregate_controller import aggregate_router
from app.statistics.controller import anime_statistics_router
from app.statistics.list_controller import list_router
from app.users.controller import user_router

main_router = APIRouter()


main_router.include_router(user_router)
main_router.include_router(auth_router)
main_router.include_router(health_router)
main_router.include_router(anime_statistics_router)
main_router.include_router(aggregate_router)
main_router.include_router(list_router)
main_router.include_router(anime_router)
main_router.include_router(options_router)
