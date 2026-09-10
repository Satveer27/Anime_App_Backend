import jwt
from app.config import settings
import structlog
from uuid import UUID
import uuid
from datetime import datetime, timedelta, timezone
from app.security.exceptions import InvalidTokenError
from app.security.utils.redis_util import track_access_token

logger = structlog.get_logger()

async def generate_access_token(user_id: UUID) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "type": "access",
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }

    await track_access_token(user_id=user_id, jti=payload["jti"], expires_at=payload["exp"])

    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)

def generate_refresh_token(user_id: UUID, expiry_time: datetime | None = None) -> str:
    now = datetime.now(timezone.utc)
    final_expiry = expiry_time if expiry_time is not None else now + timedelta(days=settings.refresh_token_expire_days)
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": final_expiry,
    }

    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)

def decode_token(encoded: str, expected_type: str) -> dict:
    try:
        payload = jwt.decode(encoded, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        logger.warning("token_decode_failed", expected_type=expected_type, error=str(exc))
        raise InvalidTokenError("Token could not be decoded") from exc

    if payload.get("type") != expected_type:
        logger.warning("token_type_mismatch", expected_type=expected_type, actual_type=payload.get("type"))
        raise InvalidTokenError(f"Expected token type '{expected_type}'")

    return payload





    