from fastapi import Depends, FastAPI
from sqlalchemy import text
import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database.database import get_db, engine
from contextlib import asynccontextmanager
from app.config import settings
from app.exceptions import AppError, handle_app_error, handle_validation_error, handle_internal_exception
from fastapi.exceptions import RequestValidationError
from app.router import main_router
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
app.add_exception_handler(AppError, handle_app_error)
app.add_exception_handler(RequestValidationError, handle_validation_error)
app.add_exception_handler(Exception, handle_internal_exception)

# health and root endpoint
@app.get("/")
async def read_root():
    return {"message": "Welcome to f1 FastAPI application!"}

@app.get("/health")
async def check_health(db: AsyncSession = Depends(get_db)):
    result = await db.execute(text("SELECT 1"))
    if result.scalar() == 1:
        return {"status": "healthy"}
    else:
        logger.error("health_check_failed", reason="db_query_returned_unexpected_result")
        return {"status": "unhealthy"}
