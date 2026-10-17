from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

DATABASE_URL = settings.db_url

engine = create_async_engine(
    DATABASE_URL,
    echo=settings.log_sql,
    # test each pooled connection before use, so one killed by a DB restart or an idle timeout is
    # replaced instead of failing a request
    pool_pre_ping=True,
    # retire connections before a proxy/firewall/server idle timeout can silently drop them
    pool_recycle=1800,
    # pool exhausted: fail in 5s (-> 503) instead of queueing requests for the 30s default
    pool_timeout=5,
    connect_args={
        "timeout": 5,  # opening a connection
        "command_timeout": 30,  # any single query
    },
)

asyncSessionMaker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


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
