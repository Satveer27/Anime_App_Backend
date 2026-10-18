import os

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.catalogue.models  # noqa: F401  (register every table on Base.metadata)
import app.ingestion.models  # noqa: F401
import app.security.models  # noqa: F401
import app.users.models  # noqa: F401
from app.core.database.database import Base


@pytest.fixture
async def pg_engine():
    """A real Postgres for the tests marked `postgres` (ADR 0006).

    Reads TEST_DB_URL and skips when it is unset, so `uv run pytest` needs no database. Every
    table is emptied before each test, so the database name must say it is a test database.
    """
    url = os.environ.get("TEST_DB_URL")
    if not url:
        pytest.skip("TEST_DB_URL is not set")
    if "test" not in (make_url(url).database or ""):
        pytest.fail("TEST_DB_URL must point at a database whose name contains 'test'")

    # NullPool: no connection outlives the test, so nothing is bound to a closed event loop.
    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield engine
    await engine.dispose()


@pytest.fixture
async def pg_sessionmaker(pg_engine):
    return async_sessionmaker(pg_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def pg_session(pg_sessionmaker):
    async with pg_sessionmaker() as session:
        yield session
