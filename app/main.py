from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    app_name = app.title
    logger.info("app_startup_beginning", app_name=app_name)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            logger.info("app_ready", app_name=app_name, environment=settings.environment)

        await redis_server.ping()
        logger.info("redis_connection_successful")

    except Exception as e:
        logger.error("app_startup_failed", error=str(e))
        raise

    yield
    logger.info("app_shutting_down", app_name=app_name)
    await redis_server.close()


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
app.add_exception_handler(Exception, handle_internal_exception)


# health and root endpoint
@app.get("/", tags=["health"], summary="Welcome message")
async def read_root():
    return {"message": "Welcome to AnimePlanet FastAPI application!"}
