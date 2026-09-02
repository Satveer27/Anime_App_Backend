import asyncio
from datetime import timedelta
from celery import Celery
from celery.schedules import crontab
from app.core.database.database import engine
from celery.signals import worker_process_init
from app.config import settings


celery_app = Celery("worker", broker=settings.redis_broker_url, backend=settings.redis_backend_url, include=["app.security.tasks.tasks", "app.users.tasks.tasks"])

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    broker_connection_retry_on_startup=True,
    result_expires=3600,  # 1 hour
    worker_concurrency=4,  # Number of worker processes
    beat_schedule={
        "cleanup_expired_refresh_tokens": {
            "task": "cleanup_expired_refresh_tokens",
            "schedule": timedelta(seconds=10),  # Run every 10 seconds
        },
    }
)

@worker_process_init.connect
def init_worker(**kwargs):
    asyncio.run(engine.dispose())  # Dispose of the engine to avoid connection issues in worker processes






