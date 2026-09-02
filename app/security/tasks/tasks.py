import structlog
import asyncio
from app.core.workers.celery_worker import celery_app
from app.core.database.database import asyncSessionMaker
from app.security.deps import create_refresh_token_repository
from app.core.database.database import engine

logger = structlog.get_logger()

@celery_app.task(name="cleanup_expired_refresh_tokens")
def cleanup_expired_refresh_tokens():
    asyncio.run(refresh_token_scheduler())


async def refresh_token_scheduler():
    async with asyncSessionMaker() as db:
        repository = create_refresh_token_repository(db)
        deleted_count = await repository.cleanup_expired_refresh_tokens()
        logger.info("expired_refresh_tokens_cleaned", count=deleted_count)
    await engine.dispose()