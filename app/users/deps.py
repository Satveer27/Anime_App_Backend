from app.security.repository import RefreshTokenRepository
from app.users.repository import UserRepository
from app.security.deps import create_refresh_token_repository
from fastapi import Depends
from app.users.service import UserService
from app.core.deps import create_user_repository

def create_user_service(user_repository: UserRepository = Depends(create_user_repository), 
                        token_repository: RefreshTokenRepository = Depends(create_refresh_token_repository)) -> UserService:
    return UserService(user_repository, token_repository)