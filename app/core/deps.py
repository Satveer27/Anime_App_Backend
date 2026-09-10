from uuid import UUID
import structlog
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from app.exceptions import AuthenticationError, ForbiddenError
from app.security.repository import RefreshTokenRepository
from app.security.utils.jwt import decode_token
from app.users.models import User
from app.users.repository import UserRepository
from app.core.database.database import get_db
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.security.utils.redis_util import is_token_revoked


logger = structlog.get_logger()
security = HTTPBearer()
optional_security = HTTPBearer(auto_error=False)

def create_user_repository(db: AsyncSession = Depends(get_db)) -> UserRepository:
    return UserRepository(db)

def create_refresh_token_repository(db: AsyncSession = Depends(get_db)) -> RefreshTokenRepository:
    return RefreshTokenRepository(db)


async def require_admin(user_repository: UserRepository = Depends(create_user_repository),
                        credentials: HTTPAuthorizationCredentials = Depends(security)) -> User:
    
    payload = decode_token(credentials.credentials, "access")
    if not payload:
        logger.warning("require_admin_invalid_token")
        raise AuthenticationError()
    
    jti = payload.get("jti")
    user_id = payload.get("sub")
    
    is_revoked = await is_token_revoked(str(jti))
    if is_revoked:
        logger.warning("require_admin_revoked_token", user_id=user_id, jti=jti)
        raise AuthenticationError()

    user = await user_repository.get_user_by_id(UUID(user_id))
    if user is None or not user.is_admin:
        logger.warning("require_admin_access_denied", user_id=user_id)
        raise ForbiddenError("Admin access required")

    logger.info("admin_access_granted", user_id=str(user.id))
    return user

async def get_current_user(user_repository: UserRepository = Depends(create_user_repository),
                           credentials: HTTPAuthorizationCredentials = Depends(security)) -> User:
    
    payload = decode_token(credentials.credentials, "access")
    if not payload:
        logger.warning("get_current_user_invalid_token")
        raise AuthenticationError()
    
    jti = payload.get("jti")
    user_id = payload.get("sub")

    is_revoked = await is_token_revoked(str(jti))
    if is_revoked:
        logger.warning("get_current_user_revoked_token", user_id=user_id, jti=jti)
        raise AuthenticationError()

    user = await user_repository.get_user_by_id(UUID(user_id))
    if user is None:
        logger.warning("get_current_user_not_found", user_id=user_id)
        raise AuthenticationError()

    logger.info("current_user_retrieved", user_id=str(user.id))
    return user