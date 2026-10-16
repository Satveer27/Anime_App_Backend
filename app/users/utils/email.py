import resend
import structlog

from app.config import settings

resend.api_key = settings.resend_api_key

logger = structlog.get_logger()

# Resend error types worth retrying even though they are 4xx
_TRANSIENT_RESEND_ERROR_TYPES = {"rate_limit_exceeded", "concurrent_idempotent_requests"}


def is_transient_email_error(exc: Exception) -> bool:
    """True if retrying later can plausibly succeed (network, 5xx, rate limit); False for
    permanent failures such as validation errors, bad API keys or exhausted quotas."""
    if isinstance(exc, resend.exceptions.ResendError):
        try:
            status = int(exc.code)
        except (TypeError, ValueError):
            status = 0
        return status >= 500 or exc.error_type in _TRANSIENT_RESEND_ERROR_TYPES
    # the SDK wraps timeouts and connection errors in a RuntimeError("Request failed: ...")
    return isinstance(exc, RuntimeError) and str(exc).startswith("Request failed")


def send_email_verification(to_email: str, code: str) -> None:
    verification_link = f"{settings.frontend_url}/verify-email?token={code}"
    params: resend.Emails.SendParams = {
        "from": "AnimePlanet <onboarding@resend.dev>",
        "to": [to_email],
        "subject": "Verify your AnimePlanet account",
        "html": f"<p>Click <a href='{verification_link}'>here</a> to verify your email. This link expires in 15 minutes.</p>",
    }
    try:
        response = resend.Emails.send(params, {"idempotency_key": f"verify-email/{code}"})
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
        response = resend.Emails.send(params, {"idempotency_key": f"password-reset/{code}"})
    except Exception as e:
        logger.error("resend_send_failed", to_email=to_email, error=str(e))
        raise
    logger.info("password_reset_email_sent", to_email=to_email, response_id=response.get("id"))
