from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.security.repository import RefreshTokenRepository
from app.users.repository import UserRepository
from app.core.database.database import get_db

def create_user_repository(db: AsyncSession = Depends(get_db)) -> UserRepository:
    return UserRepository(db)

def create_refresh_token_repository(db: AsyncSession = Depends(get_db)) -> RefreshTokenRepository:
    return RefreshTokenRepository(db)