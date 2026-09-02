import secrets
from datetime import datetime, timedelta, timezone

def generate_verification_token() -> tuple[str, datetime]:
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    return token, expires_at