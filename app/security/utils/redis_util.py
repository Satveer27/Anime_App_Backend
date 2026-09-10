from datetime import datetime, timezone
import structlog
from uuid import UUID
from app.core.redis.redis_client import redis_server

logger = structlog.get_logger()

async def track_access_token(user_id:UUID, jti: str, expires_at: datetime):
    now = datetime.now(timezone.utc).timestamp()
    await redis_server.zremrangebyscore(f"user_tokens:{user_id}", min="-inf", max=now)
    score = expires_at.timestamp()
    await redis_server.zadd(f"user_tokens:{user_id}", {jti: score})
    logger.info("access_token_tracked", user_id=str(user_id), jti=jti)

async def is_token_revoked(jti: str) -> bool:
    return await redis_server.exists(f"revoked:{jti}") == 1

async def revoke_single_access_token(jti: str, exp: float, user_id: UUID) -> None:
    now = datetime.now(timezone.utc).timestamp()
    ttl = max(int(exp - now), 0)
    await redis_server.setex(f"revoked:{jti}", ttl, "1")
    await redis_server.zrem(f"user_tokens:{user_id}", jti)
    logger.info("access_token_revoked", user_id=str(user_id), jti=jti)

async def revoke_access_to_all_tokens(user_id:UUID) -> None:
    now = datetime.now(timezone.utc).timestamp()
    await redis_server.zremrangebyscore(f"user_tokens:{user_id}", min="-inf", max=now)
    active_jtis = await redis_server.zrangebyscore(f"user_tokens:{user_id}", min=now, max="+inf")

    for jti in active_jtis:
        score = await redis_server.zscore(f"user_tokens:{user_id}", jti)
        ttl = max(int(score - now), 0)
        await redis_server.setex(f"revoked:{jti}", ttl, "1")
        await redis_server.zrem(f"user_tokens:{user_id}", jti)
        
    logger.info("all_access_tokens_revoked", user_id=str(user_id), revoked_count=len(active_jtis))