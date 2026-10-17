import asyncio

import structlog
from celery import Task
from kombu.exceptions import OperationalError

from app.exceptions import ServiceUnavailableError

logger = structlog.get_logger()


async def enqueue(task: Task, *args) -> None:
    try:
        await asyncio.to_thread(task.delay, *args)
    except OperationalError as exc:
        logger.error("task_enqueue_failed", task=task.name, error_type=type(exc).__name__)
        raise ServiceUnavailableError("Could not send the email, please try again.") from None
