
from sqlalchemy.ext.asyncio import AsyncSession
from app.users.models import User
from sqlalchemy import select, func
from uuid import UUID
from datetime import datetime

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

    async def get_users(self, 
                        limit: int, 
                        offset: int, 
                        username: str | None = None, 
                        email: str | None = None, 
                        is_admin: bool| None = None,
                        created_after: datetime | None = None,
                        created_before: datetime | None = None,
                        username_sorted_bool: bool = False) -> list[User]:
        query = select(User)

        if username is not None:
            query = query.where(User.username.ilike(f"{username}%"))

        if email is not None:
            query = query.where(User.email.ilike(f"{email}%"))

        if is_admin is not None:
            query = query.where(User.is_admin == is_admin)

        if created_after is not None:
            query = query.where(User.created_at >= created_after)

        if created_before is not None:
            query = query.where(User.created_at <= created_before)

        if username_sorted_bool:
            query = query.order_by(User.username)
        else:
            query = query.order_by(User.created_at)

        query = query.limit(limit).offset(offset)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def count_users(self,
                         username: str | None = None, 
                         email: str | None = None, 
                         is_admin: bool| None = None,
                         created_after: datetime | None = None,
                         created_before: datetime | None = None)-> int:
        
        query = select(func.count()).select_from(User)

        if username is not None:
            query = query.where(User.username.ilike(f"{username}%"))
        
        if email is not None:
            query = query.where(User.email.ilike(f"{email}%"))
        
        if is_admin is not None:
            query = query.where(User.is_admin == is_admin)
        
        if created_after is not None:
            query = query.where(User.created_at >= created_after)
        
        if created_before is not None:
            query = query.where(User.created_at <= created_before)

        result = await self.db.execute(query)
        return result.scalar_one()

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