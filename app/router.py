from fastapi import APIRouter
from app.users.controller import user_router
from app.security.controller import auth_router
main_router = APIRouter()


main_router.include_router(user_router)
main_router.include_router(auth_router)