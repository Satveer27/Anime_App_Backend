import structlog
from celery import Task

from app.core.workers.celery_worker import celery_app
from app.users.utils.email import send_email_password_reset, send_email_verification

logger = structlog.get_logger()


class EmailTask(Task):
    def on_failure(self, exc, task_id, args, kwargs, einfo):
        # only reached once all retries are exhausted
        logger.error("email_task_failed_permanently", task=self.name, error=str(exc))


# Retry transient failures with exponential backoff (1s, 2s, 4s...). The send helpers pass a
# Resend idempotency key, so a retry after a lost response cannot send a duplicate email.
EMAIL_RETRY_POLICY = {
    "autoretry_for": (Exception,),
    "retry_backoff": True,
    "retry_backoff_max": 300,
    "retry_jitter": True,
    "max_retries": 5,
}


@celery_app.task(name="send_email_verification_task", base=EmailTask, **EMAIL_RETRY_POLICY)
def send_email_verification_task(to_email: str, code: str) -> None:
    send_email_verification(to_email, code)
    logger.info("email_verification_task_completed")


@celery_app.task(name="send_email_password_reset_task", base=EmailTask, **EMAIL_RETRY_POLICY)
def send_email_password_reset_task(to_email: str, code: str) -> None:
    send_email_password_reset(to_email, code)
    logger.info("password_reset_task_completed")
