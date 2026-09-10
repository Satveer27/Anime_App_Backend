import structlog
from app.core.workers.celery_worker import celery_app
from app.users.utils.email import send_email_password_reset, send_email_verification


logger = structlog.get_logger()

@celery_app.task(name="send_email_verification_task")
def send_email_verification_task(to_email:str, code:str) -> None:
    try:
        send_email_verification(to_email, code)
        logger.info("email_verification_task_completed", to_email=to_email)
    except Exception as e:
        logger.error("email_verification_failed", to_email=to_email, error=str(e))

@celery_app.task(name="send_email_password_reset_task")
def send_email_password_reset_task(to_email:str, code:str) -> None:
    try:
        send_email_password_reset(to_email, code)
        logger.info("password_reset_task_completed", to_email=to_email)
    except Exception as e:
        logger.error("password_reset_failed", to_email=to_email, error=str(e))