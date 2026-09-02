import resend
from app.config import settings
import structlog 

resend.api_key = settings.resend_api_key

logger = structlog.get_logger()

def send_email_verification(to_email: str, code: str) -> None:
    verification_link = f"{settings.frontend_url}/verify-email?token={code}"
    params: resend.Emails.SendParams = {
    "from": "F1planet <onboarding@resend.dev>",
    "to": [to_email],
    "subject": "Verify your F1Planet account",
    "html": f"<p>Click <a href='{verification_link}'>here</a> to verify your email. This link expires in 15 minutes.</p>",
    }
    response = resend.Emails.send(params)
    logger.info("verification_email_sent", to_email=to_email, response_id=response.get("id"))