import asyncio

import structlog

from app.core.database.database import asyncSessionMaker, engine
from app.core.workers.celery_worker import celery_app
from app.security.deps import create_refresh_token_repository

logger = structlog.get_logger()


@celery_app.task(
    name="cleanup_expired_refresh_tokens",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    max_retries=2,
)
def cleanup_expired_refresh_tokens():
    try:
        asyncio.run(refresh_token_scheduler())
    except Exception as e:
        logger.error("refresh_token_cleanup_failed", error=str(e))
        raise


async def refresh_token_scheduler():
    try:
        async with asyncSessionMaker() as db:
            repository = create_refresh_token_repository(db)
            deleted_count = await repository.cleanup_expired_refresh_tokens()
            await db.commit()
            logger.info("expired_refresh_tokens_cleaned", count=deleted_count)
    finally:
        await engine.dispose()
