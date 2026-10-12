from fastapi import Depends

from app.core.deps import create_refresh_token_repository, create_user_repository
from app.security.repository import RefreshTokenRepository
from app.security.service import JWTService
from app.users.repository import UserRepository


def create_jwt_service(
    refresh_token_repository: RefreshTokenRepository = Depends(create_refresh_token_repository),
    user_repository: UserRepository = Depends(create_user_repository),
) -> JWTService:
    return JWTService(refresh_token_repository, user_repository)
