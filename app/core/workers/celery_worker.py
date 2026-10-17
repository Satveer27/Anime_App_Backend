import asyncio

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_process_init

from app.config import settings
from app.core.database.database import engine

celery_app = Celery(
    "worker",
    broker=settings.redis_broker_url,
    backend=settings.redis_backend_url,
    include=["app.security.tasks.tasks", "app.users.tasks.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    broker_connection_retry_on_startup=True,
    # Publishing from the API must fail fast when the broker is down: ~2s per attempt, two retry.
    broker_connection_timeout=2,
    broker_transport_options={"socket_connect_timeout": 2, "socket_timeout": 2},
    task_publish_retry=True,
    task_publish_retry_policy={
        "max_retries": 2,
        "interval_start": 0,
        "interval_step": 0.2,
        "interval_max": 0.2,
    },
    result_expires=3600,  # 1 hour
    worker_concurrency=4,  # Number of worker processes
    beat_schedule={
        "cleanup_expired_refresh_tokens": {
            "task": "cleanup_expired_refresh_tokens",
            "schedule": crontab(day_of_week="sun", hour=0, minute=0),  # Every Sunday at midnight
        },
    },
)


@worker_process_init.connect
def init_worker(**kwargs):
    asyncio.run(
        engine.dispose()
    )  # Dispose of the engine to avoid connection issues in worker processes
