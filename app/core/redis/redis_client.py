import redis.asyncio as redis

from app.config import settings

# Timeouts keep a stalled Redis from hanging requests; callers see a RedisError (mapped to 503).
redis_server = redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=2,
    socket_timeout=2,
)
