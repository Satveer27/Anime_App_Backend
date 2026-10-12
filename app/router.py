from fastapi import APIRouter

from app.security.controller import auth_router
from app.users.controller import user_router

main_router = APIRouter()


main_router.include_router(user_router)
main_router.include_router(auth_router)
