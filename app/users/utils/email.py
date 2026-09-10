import resend
from app.config import settings
import structlog 

resend.api_key = settings.resend_api_key

logger = structlog.get_logger()

def send_email_verification(to_email: str, code: str) -> None:
    verification_link = f"{settings.frontend_url}/verify-email?token={code}"
    params: resend.Emails.SendParams = {
    "from": "AnimePlanet <onboarding@resend.dev>",
    "to": [to_email],
    "subject": "Verify your AnimePlanet account",
    "html": f"<p>Click <a href='{verification_link}'>here</a> to verify your email. This link expires in 15 minutes.</p>",
    }
    try:
        response = resend.Emails.send(params)
    except Exception as e:
        logger.error("resend_send_failed", to_email=to_email, error=str(e))
        raise
    logger.info("verification_email_sent", to_email=to_email, response_id=response.get("id"))


def send_email_password_reset(to_email: str, code: str) -> None:
    reset_link = f"{settings.frontend_url}/reset-password?token={code}"
    params: resend.Emails.SendParams = {
    "from": "AnimePlanet <onboarding@resend.dev>",
    "to": [to_email],
    "subject": "Reset your AnimePlanet password",
    "html": f"<p>Click <a href='{reset_link}'>here</a> to reset your password. This link expires in 15 minutes.</p>",
    }
    try:
        response = resend.Emails.send(params)
    except Exception as e:
        logger.error("resend_send_failed", to_email=to_email, error=str(e))
        raise
    logger.info("password_reset_email_sent", to_email=to_email, response_id=response.get("id"))