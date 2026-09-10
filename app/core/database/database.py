from typing import AsyncGenerator
from app.config import settings
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase


DATABASE_URL = settings.db_url

engine = create_async_engine(DATABASE_URL, echo=settings.log_sql)

asyncSessionMaker = async_sessionmaker(engine, class_= AsyncSession, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

# create function to essentially creat a session for each request
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with asyncSessionMaker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
