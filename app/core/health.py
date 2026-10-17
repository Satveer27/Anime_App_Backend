import asyncio

import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.database.database import engine
from app.core.redis.redis_client import redis_server

logger = structlog.get_logger()

router = APIRouter(tags=["health"])

CHECK_TIMEOUT_S = 2.0


async def _check_database() -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def _check_redis() -> None:
    await redis_server.ping()


async def _run_check(name: str, check) -> bool:
    try:
        await asyncio.wait_for(check(), timeout=CHECK_TIMEOUT_S)
    except Exception as exc:
        logger.error("health_check_failed", dependency=name, error_type=type(exc).__name__)
        return False
    return True


@router.get("/health/live", summary="Liveness: the process is up")
async def liveness():
    return {"status": "alive"}


@router.get(
    "/health",
    summary="Readiness: Postgres and Redis are reachable",
    responses={503: {"description": "A required dependency is unreachable"}},
)
async def readiness():
    db_ok, redis_ok = await asyncio.gather(
        _run_check("database", _check_database),
        _run_check("redis", _check_redis),
    )
    healthy = db_ok and redis_ok
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "healthy" if healthy else "unhealthy",
            "checks": {
                "database": "ok" if db_ok else "down",
                "redis": "ok" if redis_ok else "down",
            },
        },
    )
