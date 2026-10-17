import structlog
from celery import Task

from app.core.workers.celery_worker import celery_app
from app.users.utils.email import (
    is_transient_email_error,
    send_email_password_reset,
    send_email_verification,
)

logger = structlog.get_logger()


class EmailTask(Task):
    def on_failure(self, exc, task_id, args, kwargs, einfo):
        # only reached once all retries are exhausted
        logger.error(
            "email_task_failed_permanently",
            task=self.name,
            task_id=task_id,
            to_email=args[0] if args else None,
            error=str(exc),
        )


class TransientEmailError(Exception):
    """Marks a send failure that is worth retrying (network, 5xx, rate limit)."""


# Retry only transient failures with exponential backoff (1s, 2s, 4s...); permanent ones such as
# validation errors fail immediately. The send helpers pass a Resend idempotency key, so a retry
# after a lost response cannot send a duplicate email.
EMAIL_RETRY_POLICY = {
    "autoretry_for": (TransientEmailError,),
    "retry_backoff": True,
    "retry_backoff_max": 300,
    "retry_jitter": True,
    "max_retries": 5,
}


def _send(sender, to_email: str, code: str) -> None:
    try:
        sender(to_email, code)
    except Exception as exc:
        if is_transient_email_error(exc):
            raise TransientEmailError(str(exc)) from exc
        raise


@celery_app.task(name="send_email_verification_task", base=EmailTask, **EMAIL_RETRY_POLICY)
def send_email_verification_task(to_email: str, code: str) -> None:
    _send(send_email_verification, to_email, code)
    logger.info("email_verification_task_completed")


@celery_app.task(name="send_email_password_reset_task", base=EmailTask, **EMAIL_RETRY_POLICY)
def send_email_password_reset_task(to_email: str, code: str) -> None:
    _send(send_email_password_reset, to_email, code)
    logger.info("password_reset_task_completed")
