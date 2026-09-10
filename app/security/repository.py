from sqlalchemy.ext.asyncio import AsyncSession
from app.security.models import RefreshToken
from datetime import datetime, timezone
from sqlalchemy import select, delete, update
from uuid import UUID

class RefreshTokenRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_refresh_token(self, refresh_token: RefreshToken)-> RefreshToken:
        self.db.add(refresh_token)
        await self.db.flush()
        await self.db.refresh(refresh_token)
        return refresh_token

    async def update_refresh_token(self, refresh_token: RefreshToken) -> RefreshToken:
        self.db.add(refresh_token)
        await self.db.flush()
        await self.db.refresh(refresh_token)
        return refresh_token

    async def update_refresh_token_to_revoke(self, user_id: UUID) -> int:
        result = await self.db.execute(update(RefreshToken).where(RefreshToken.user_id == user_id, RefreshToken.revoke == False).values(revoke=True))
        return result.rowcount
        

    async def delete_refresh_token(self, refresh_token: RefreshToken) -> None:
        await self.db.delete(refresh_token)

    async def delete_all_refresh_tokens_for_user(self, user_id: UUID) -> None:
        await self.db.execute(delete(RefreshToken).where(RefreshToken.user_id == user_id))

    async def get_refresh_token_by_jti(self, refresh_id: UUID) -> RefreshToken | None:
        result =  await self.db.execute(select(RefreshToken).where(RefreshToken.jti == refresh_id))
        return result.scalar_one_or_none()

    async def get_refresh_token_by_user_id(self, user_id: UUID) -> list[RefreshToken]:
            result =  await self.db.execute(select(RefreshToken).where(RefreshToken.user_id == user_id))
            return list(result.scalars().all())

    async def cleanup_expired_refresh_tokens(self) -> int:
        now = datetime.now(timezone.utc)
        result = await self.db.execute(delete(RefreshToken).where(RefreshToken.expires_at < now))
        return result.rowcount