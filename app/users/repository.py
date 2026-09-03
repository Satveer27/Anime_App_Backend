
from sqlalchemy.ext.asyncio import AsyncSession
from app.users.models import User
from sqlalchemy import select
from uuid import UUID

class UserRepository:
    def __init__(self, db:AsyncSession):
        self.db = db

    async def create_user(self, user:User) -> User:
        self.db.add(user)
        await self.db.flush()
        await self.db.refresh(user)
        return user

    async def get_user_by_email(self, email:str) -> User | None:
        result = await self.db.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def get_user_by_id(self, user_id:UUID) -> User | None:
        result = await self.db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def get_users(self) -> list[User]:
        result = await self.db.execute(select(User))
        return list(result.scalars().all())

    async def get_user_by_username(self, username:str) -> User | None:
        result = await self.db.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none()

    async def update_user(self, user:User) -> User:
        self.db.add(user)
        await self.db.flush()
        await self.db.refresh(user)
        return user

    async def delete_user(self, user:User) -> None:
        await self.db.delete(user)