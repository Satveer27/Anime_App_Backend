"""A small Redis response cache for the public statistics and anime endpoints (#55).

Rules, from the issue and the `noeviction` decision:
- Every key has a TTL, so nothing lives forever.
- A key is built only from validated enums, ints, bools and None (`make_key` refuses anything
  else, so free text or a list can never become a key), and each endpoint caches only the
  requests whose key space is small (`cacheable`).
- A cache failure never fails a request: a Redis error, a bad stored value or a full Redis
  (`noeviction` rejects writes) falls through to the database.
- A response over `max_value_bytes` is not stored, because Redis never evicts.
"""

import functools
import inspect
import time
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import TypeVar

import structlog
from pydantic import BaseModel, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config import settings
from app.core.redis.redis_client import redis_server

logger = structlog.get_logger()

KEY_PREFIX = "cache:v1"
# A Redis outage would log on every request, so a failure is logged at most this often.
FAILURE_LOG_INTERVAL_SECONDS = 30

ModelT = TypeVar("ModelT", bound=BaseModel)


def _key_value(part_name: str, value) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    if value is None or isinstance(value, bool | int):
        return str(value)
    if isinstance(value, list | tuple) and all(isinstance(item, Enum | int) for item in value):
        # A filter with several values (`status=airing&status=upcoming`): sorted, so the order
        # the client sent them in does not make a second key.
        return "+".join(sorted(_key_value(part_name, item) for item in value))
    raise TypeError(
        f"cache key part {part_name!r} must be an enum, int, bool, None or a list of enums or ints"
    )


def make_key(name: str, **parts) -> str:
    """`cache:v1:name:a=1:b=None`, from enums, ints, bools, None and lists of enums or ints.

    Free text and floats are refused so a request value can never create an unbounded or
    attacker-chosen key. A list is allowed only because the endpoint's `cacheable` rule limits
    which lists reach this point.
    """
    pieces = [
        f"{part_name}={_key_value(part_name, value)}" for part_name, value in sorted(parts.items())
    ]
    return ":".join([KEY_PREFIX, name, *pieces])


class ResponseCache:
    def __init__(self, redis: Redis, *, enabled: bool, ttl_seconds: int, max_value_bytes: int):
        self.redis = redis
        self.enabled = enabled
        self.ttl_seconds = ttl_seconds
        self.max_value_bytes = max_value_bytes
        self._last_failure_log = float("-inf")

    def _failed(self, operation: str, key: str, error: Exception) -> None:
        now = time.monotonic()
        if now - self._last_failure_log >= FAILURE_LOG_INTERVAL_SECONDS:
            self._last_failure_log = now
            logger.warning(
                "cache_unavailable", operation=operation, key=key, error=type(error).__name__
            )

    async def _read(self, key: str, model: type[ModelT]) -> ModelT | None:
        try:
            raw = await self.redis.get(key)
        except RedisError as error:
            self._failed("get", key, error)
            return None
        if raw is None:
            return None
        try:
            return model.model_validate_json(raw)
        except (ValidationError, ValueError) as error:
            # A value from an older response shape: treat it as a miss, it expires by its TTL.
            self._failed("parse", key, error)
            return None

    async def _write(self, key: str, response: BaseModel) -> None:
        payload = response.model_dump_json()
        if len(payload.encode()) > self.max_value_bytes:
            logger.info("cache_skipped_too_large", key=key, bytes=len(payload.encode()))
            return
        try:
            await self.redis.set(key, payload, ex=self.ttl_seconds)
        except RedisError as error:
            self._failed("set", key, error)

    async def get_or_set(
        self,
        key: str,
        model: type[ModelT],
        loader: Callable[[], Awaitable[ModelT]],
        store_if: Callable[[ModelT], bool] | None = None,
    ) -> ModelT:
        """The cached response for `key`, or the loader's, stored for the next request."""
        if not self.enabled:
            return await loader()
        cached = await self._read(key, model)
        if cached is not None:
            return cached
        response = await loader()
        if store_if is None or store_if(response):
            await self._write(key, response)
        return response


response_cache = ResponseCache(
    redis_server,
    enabled=settings.cache_enabled,
    ttl_seconds=settings.cache_ttl_seconds,
    max_value_bytes=settings.cache_max_value_bytes,
)


def get_response_cache() -> ResponseCache:
    return response_cache


def cached(
    name: str,
    model: type[BaseModel],
    *,
    cacheable: Callable[[dict], bool] = lambda args: True,
    store_if: Callable[[BaseModel], bool] | None = None,
):
    """Cache a service method's response when `self.cache` is set and the request is cacheable.

    `cacheable` gets the call's arguments by name (defaults applied) and says whether the key
    space of this request is small enough to cache. Without a cache the method runs as before.
    """

    def decorator(method):
        signature = inspect.signature(method)

        @functools.wraps(method)
        async def wrapper(self, *args, **kwargs):
            cache = getattr(self, "cache", None)
            if cache is None or not cache.enabled:
                return await method(self, *args, **kwargs)
            bound = signature.bind(self, *args, **kwargs)
            bound.apply_defaults()
            arguments = dict(list(bound.arguments.items())[1:])
            if not cacheable(arguments):
                return await method(self, *args, **kwargs)
            return await cache.get_or_set(
                make_key(name, **arguments),
                model,
                lambda: method(self, *args, **kwargs),
                store_if,
            )

        return wrapper

    return decorator
