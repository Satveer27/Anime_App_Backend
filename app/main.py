from fastapi import Depends, FastAPI
from sqlalchemy import text
import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database.database import get_db, engine
from contextlib import asynccontextmanager
from app.config import settings
from app.exceptions import (
    TooManyRequestsError,
    handle_authentication_error,
    handle_duplicate_resource_error,
    handle_resource_does_not_exist_exception,
    handle_auth_token_error,
    handle_already_logged_in_error,
    handle_too_many_requests,
    handle_unprocessable_entity_exception,
    handle_internal_exception,
)
from app.exceptions import(
    DuplicateResourceError,
    ResourceDoesNotExistError,
    AuthTokenError,
    ConflictLoggingIn,
    AuthenticationError,
)
from app.router import main_router
from fastapi.exceptions import RequestValidationError
from app.core.redis.redis_client import redis_server

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


app = FastAPI(title="F1 FastAPI Application", lifespan=lifespan)

# Routes
app.include_router(main_router)


#Exception
app.add_exception_handler(Exception, handle_internal_exception)
app.add_exception_handler(DuplicateResourceError, handle_duplicate_resource_error)
app.add_exception_handler(RequestValidationError, handle_unprocessable_entity_exception)
app.add_exception_handler(ResourceDoesNotExistError, handle_resource_does_not_exist_exception)
app.add_exception_handler(AuthTokenError, handle_auth_token_error)
app.add_exception_handler(ConflictLoggingIn, handle_already_logged_in_error)
app.add_exception_handler(AuthenticationError, handle_authentication_error)
app.add_exception_handler(TooManyRequestsError, handle_too_many_requests)

# health and root endpoint
@app.get("/")
async def read_root():
    return {"message": "Welcome to f1 FastAPI application!"}

@app.get("/health")
async def check_health(db: AsyncSession = Depends(get_db)):
    # Perform any necessary health checks here
    result = await db.execute(text("SELECT 1"))
    if result.scalar() == 1:
        return {"status": "healthy"}
    else:
        return {"status": "unhealthy"}
