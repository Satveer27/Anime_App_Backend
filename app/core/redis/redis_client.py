import redis.asyncio as redis

from app.config import settings

redis_server = redis.from_url(settings.redis_url, decode_responses=True)