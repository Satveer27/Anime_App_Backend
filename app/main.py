import asyncio
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.core.database.database import engine
from app.core.redis.redis_client import redis_server
from app.exceptions import (
    AppError,
    handle_app_error,
    handle_dependency_unavailable,
    handle_http_exception,
    handle_internal_exception,
    handle_validation_error,
)
from app.router import main_router

logger = structlog.get_logger()


STARTUP_ATTEMPTS = 3  # first try plus 2 retries
STARTUP_RETRY_DELAY_S = 2


async def _check_dependencies() -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    await redis_server.ping()


async def _wait_for_dependencies() -> None:
    """A dependency that is a few seconds late during a deploy should not crash-loop the app."""
    for attempt in range(1, STARTUP_ATTEMPTS + 1):
        try:
            await _check_dependencies()
            return
        except Exception as exc:
            if attempt == STARTUP_ATTEMPTS:
                logger.error("app_startup_failed", attempts=attempt, error=str(exc))
                raise
            delay = STARTUP_RETRY_DELAY_S * attempt
            logger.warning(
                "startup_dependency_check_failed",
                attempt=attempt,
                retry_in_s=delay,
                error_type=type(exc).__name__,
            )
            await asyncio.sleep(delay)


async def _close_clients() -> None:
    # separate try blocks: a failing Redis close must not leave the DB pool open
    try:
        await redis_server.aclose()
    except Exception as exc:
        logger.warning("redis_close_failed", error_type=type(exc).__name__)
    try:
        await engine.dispose()
    except Exception as exc:
        logger.warning("engine_dispose_failed", error_type=type(exc).__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app_name = app.title
    logger.info("app_startup_beginning", app_name=app_name)
    try:
        await _wait_for_dependencies()
        logger.info("app_ready", app_name=app_name, environment=settings.environment)
        yield
    finally:
        logger.info("app_shutting_down", app_name=app_name)
        await _close_clients()


app = FastAPI(
    title="AnimePlanet FastAPI Application",
    description="Authentication, user management, and admin API for the AnimePlanet backend.",
    version="1.0.0",
    lifespan=lifespan,
    openapi_tags=[
        {
            "name": "auth",
            "description": "Login, refresh, and logout. Issues short-lived JWT access tokens "
            "and rotates an HttpOnly refresh-token cookie scoped to /api/v1/auth.",
        },
        {
            "name": "users",
            "description": "Signup, email verification, password/email management, and "
            "admin-only user administration (listing, editing, deleting).",
        },
    ],
)

# Routes
app.include_router(main_router, prefix="/api/v1")


# Exception
app.add_exception_handler(AppError, handle_app_error)
app.add_exception_handler(RequestValidationError, handle_validation_error)
app.add_exception_handler(StarletteHTTPException, handle_http_exception)
app.add_exception_handler(RedisError, handle_dependency_unavailable)
app.add_exception_handler(DBAPIError, handle_dependency_unavailable)
# asyncpg raises a bare ConnectionRefusedError (not a DBAPIError) when Postgres is unreachable
app.add_exception_handler(ConnectionError, handle_dependency_unavailable)
app.add_exception_handler(TimeoutError, handle_dependency_unavailable)
app.add_exception_handler(SQLAlchemyTimeoutError, handle_dependency_unavailable)
app.add_exception_handler(Exception, handle_internal_exception)


# health and root endpoint
@app.get("/", tags=["health"], summary="Welcome message")
async def read_root():
    return {"message": "Welcome to AnimePlanet FastAPI application!"}
